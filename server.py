#!/usr/bin/env python3
"""LifeOS Pocket: a loopback-only expense and loan ledger. No dependencies."""
import argparse
import datetime as dt
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import urlparse, parse_qs
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Kolkata')
MARKER = '<!-- lifeos-pocket-generated:v1 -->'
CATEGORIES = ['Shopping', 'Food & groceries', 'Transport', 'Home & bills', 'Health', 'Travel', 'Learning', 'Other']


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def money(value):
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed <= 0 or parsed > Decimal('999999999.99'):
            raise ValueError()
        if parsed != parsed.quantize(Decimal('.01')):
            raise ValueError()
        return int(parsed * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Enter a positive amount with at most two decimal places.')


def clean(value, limit=500, required=False):
    if not isinstance(value, str):
        raise ValueError('Text fields must contain text.')
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f'Enter text between {1 if required else 0} and {limit} characters.')
    return value


def date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Choose a valid date.')
    return dt.date.fromisoformat(value).isoformat()


def due(value):
    if not value:
        return ''
    if not isinstance(value, str) or len(value) != 16:
        raise ValueError('Choose a valid reminder date and time.')
    return dt.datetime.strptime(value, '%Y-%m-%dT%H:%M').strftime('%Y-%m-%dT%H:%M')


def snapshot(conn):
    entries = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM entries ORDER BY rowid DESC')]
    repayments = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM repayments ORDER BY rowid DESC')]
    return {'schemaVersion': 1, 'currency': 'INR', 'timezone': 'Asia/Kolkata', 'entries': entries, 'repayments': repayments}


def remaining(entry, repayments):
    return entry['amountMinor'] - sum(r['amountMinor'] for r in repayments if r['entryId'] == entry['id'])


def md(value):
    value = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    for char in ['\\', '|', '[', ']', '*', '_', '`', '#']:
        value = value.replace(char, '\\' + char)
    return value.replace('\r', ' ').replace('\n', '<br>')


def rupees(minor):
    return f'₹{minor / 100:,.2f}'


class Ledger:
    def __init__(self, data_dir, vault):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = data_dir / 'ledger.sqlite3'
        self.vault = Path(vault).expanduser() if vault else None
        self.lock = threading.RLock()
        self.sync = {'ok': False, 'enabled': bool(vault), 'message': 'Obsidian sync is not configured.'}
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS repayments (id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES entries(id), payload TEXT NOT NULL)')
        self.sync_note()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def state(self):
        with self.lock, self.connect() as conn:
            return {**snapshot(conn), 'sync': self.sync, 'serverTime': now()}

    def save_entry(self, data):
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = None
            if data.get('id'):
                row = conn.execute('SELECT payload FROM entries WHERE id=?', (data['id'],)).fetchone()
                if not row:
                    raise ValueError('Entry not found. Refresh and try again.')
                old = json.loads(row[0])
            kind = data.get('kind')
            if kind not in ['expense', 'loan', 'reminder']:
                raise ValueError('Choose expense, money lent, or reminder.')
            amount = 0 if kind == 'reminder' else money(data.get('amount'))
            reminder = due(data.get('dueAt', ''))
            if kind == 'reminder' and not reminder:
                raise ValueError('Choose a date and time for this reminder.')
            category = data.get('category', 'Other')
            if category not in CATEGORIES:
                raise ValueError('Choose a valid category.')
            if old:
                repayments = snapshot(conn)['repayments']
                paid = sum(r['amountMinor'] for r in repayments if r['entryId'] == old['id'])
                if paid and (kind != 'loan' or amount < paid):
                    raise ValueError('This loan has repayments. Keep it as a loan and do not reduce it below the amount repaid.')
            entry = {
                'id': old['id'] if old else str(uuid.uuid4()), 'kind': kind,
                'title': clean(data.get('title', ''), 200, True), 'amountMinor': amount,
                'person': clean(data.get('person', ''), 100, kind == 'loan') if kind == 'loan' else '',
                'category': category if kind == 'expense' else '',
                'date': date(data.get('date')), 'dueAt': reminder,
                'notes': clean(data.get('notes', ''), 2000),
                'reminderDone': old['reminderDone'] if old and old['dueAt'] == reminder and old['kind'] == kind else False,
                'createdAt': old['createdAt'] if old else now(), 'updatedAt': now(),
            }
            conn.execute('INSERT INTO entries(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (entry['id'], json.dumps(entry)))
        self.sync_note()
        return self.state()

    def repay(self, data):
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            state = snapshot(conn)
            entry = next((e for e in state['entries'] if e['id'] == data.get('entryId') and e['kind'] == 'loan'), None)
            if not entry:
                raise ValueError('Loan not found.')
            amount = money(data.get('amount'))
            if amount > remaining(entry, state['repayments']):
                raise ValueError('Repayment exceeds the outstanding loan amount.')
            repayment = {'id': str(uuid.uuid4()), 'entryId': entry['id'], 'amountMinor': amount, 'date': date(data.get('date')), 'notes': clean(data.get('notes', ''), 500), 'createdAt': now()}
            conn.execute('INSERT INTO repayments(id,entry_id,payload) VALUES (?,?,?)', (repayment['id'], entry['id'], json.dumps(repayment)))
        self.sync_note()
        return self.state()

    def toggle_reminder(self, data):
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM entries WHERE id=?', (data.get('id'),)).fetchone()
            if not row:
                raise ValueError('Reminder not found.')
            entry = json.loads(row[0])
            if not entry['dueAt'] or type(data.get('done')) is not bool:
                raise ValueError('Invalid reminder update.')
            entry.update(reminderDone=data['done'], updatedAt=now())
            conn.execute('UPDATE entries SET payload=? WHERE id=?', (json.dumps(entry), entry['id']))
        self.sync_note()
        return self.state()

    def sync_note(self):
        if not self.vault:
            return
        with self.lock:
            try:
                with self.connect() as conn:
                    state = snapshot(conn)
                folder = self.vault / 'ai zone/openai agent/Personal/Finance'
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / 'Expense Ledger.md'
                if target.exists() and '<!-- pocket-vps-sync:v1 -->' in target.read_text():
                    raise ValueError('This note is synchronized from your VPS. Use the online Pocket app for new records.')
                if target.exists() and MARKER not in target.read_text():
                    raise ValueError('Expense Ledger.md already exists and is not managed by this tracker.')
                expenses = [e for e in state['entries'] if e['kind'] == 'expense']
                loans = [e for e in state['entries'] if e['kind'] == 'loan']
                active = [e for e in state['entries'] if e['dueAt'] and not e['reminderDone'] and (e['kind'] != 'loan' or remaining(e, state['repayments']) > 0)]
                lines = [MARKER, '# Expense Ledger', '', f'Updated: {now()}', '', 'Generated from LifeOS Pocket. Edit entries in the tracker; this note is refreshed after every change.', '', 'Currency: INR · Reminder timezone: Asia/Kolkata', '', f'- Total expenses: {rupees(sum(e["amountMinor"] for e in expenses))}', f'- Outstanding loans: {rupees(sum(remaining(e, state["repayments"]) for e in loans))}', f'- Open reminders: {len(active)}', '', '## Expenses', '', '| Date | Item | Category | Amount | Notes |', '| --- | --- | --- | ---: | --- |']
                for e in expenses:
                    lines.append(f'| {e["date"]} | {md(e["title"])} | {md(e["category"])} | {rupees(e["amountMinor"])} | {md(e["notes"])} |')
                lines += ['', '## Money lent', '', '| Date | Person | Purpose | Lent | Outstanding | Reminder | Notes |', '| --- | --- | --- | ---: | ---: | --- | --- |']
                for e in loans:
                    lines.append(f'| {e["date"]} | {md(e["person"])} | {md(e["title"])} | {rupees(e["amountMinor"])} | {rupees(remaining(e,state["repayments"]))} | {e["dueAt"].replace("T"," ") or "Not set"} | {md(e["notes"])} |')
                lines += ['', '## Repayments received', '', '| Date | Person | Amount | Notes |', '| --- | --- | ---: | --- |']
                for r in state['repayments']:
                    e = next(e for e in loans if e['id'] == r['entryId'])
                    lines.append(f'| {r["date"]} | {md(e["person"])} | {rupees(r["amountMinor"])} | {md(r["notes"])} |')
                lines += ['', '## Reminders', '', 'Reminder dates appear in the app. Calendar alerts require importing the downloaded calendar event.', '']
                for e in sorted((e for e in state['entries'] if e['dueAt']), key=lambda e:e['dueAt']):
                    settled = e['kind'] == 'loan' and remaining(e, state['repayments']) == 0
                    done = e['reminderDone'] or settled
                    label = 'Loan settled' if settled else ('Completed by user' if e['reminderDone'] else 'Open')
                    lines += [f'- [{"x" if done else " "}] {md(e["title"])}', f'  - Due: {e["dueAt"].replace("T"," ")} IST', f'  - Status: {label}', f'  - Notes: {md(e["notes"]) or "—"}']
                if not state['entries']:
                    lines += ['No entries recorded yet.']
                lines += ['', 'Related: [[ai zone/openai agent/Personal/Finance/Expense Tracker|Expense Tracker]] · [[ai zone/openai agent/LifeOS Home|LifeOS Home]]', '']
                content = '\n'.join(lines)
                temporary = target.with_suffix('.md.tmp')
                temporary.write_text(content)
                temporary.replace(target)
                if target.read_text() != content:
                    raise OSError('Note read-back verification failed.')
                self.sync = {'ok': True, 'enabled': True, 'message': 'Saved to Obsidian', 'updatedAt': now()}
            except (OSError, ValueError) as error:
                self.sync = {'ok': False, 'enabled': True, 'message': f'Saved locally. Obsidian sync needs attention: {error}'}


def ical(entry):
    def escape(text):
        return text.replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')
    start = dt.datetime.fromisoformat(entry['dueAt']).replace(tzinfo=TZ).astimezone(dt.timezone.utc)
    title = f'Collect repayment from {entry["person"]}: {entry["title"]}' if entry['kind'] == 'loan' else entry['title']
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//LifeOS//Pocket//EN','CALSCALE:GREGORIAN','BEGIN:VEVENT',f'UID:{entry["id"]}@lifeos.local',f'DTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}',f'DTSTART:{start:%Y%m%dT%H%M%SZ}',f'DTEND:{start + dt.timedelta(minutes=15):%Y%m%dT%H%M%SZ}',f'SUMMARY:{escape(title)}',f'DESCRIPTION:{escape(entry["notes"])}','BEGIN:VALARM','ACTION:DISPLAY','TRIGGER:PT0S',f'DESCRIPTION:{escape(title)}','END:VALARM','END:VEVENT','END:VCALENDAR']
    folded = []
    for line in lines:
        current = ''
        for char in line:
            if len((current + char).encode()) > 73:
                folded.append(current)
                current = ' '
            current += char
        folded.append(current)
    return ('\r\n'.join(folded) + '\r\n').encode()


def main():
    config_path = ROOT / 'config.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--vault', default=config.get('vault', ''))
    args = parser.parse_args()
    ledger = Ledger(args.data_dir, args.vault)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass  # Never write financial entries or request data to logs.

        def reply(self, status, body, content_type='application/json; charset=utf-8', filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            if self.headers.get('Host') not in [f'127.0.0.1:{args.port}', f'localhost:{args.port}']:
                self.reply(403, {'error': 'Local access only.'})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            parsed = urlparse(self.path)
            if parsed.path in ['/', '/app.js', '/style.css']:
                name = 'index.html' if parsed.path == '/' else parsed.path[1:]
                content = (ROOT / name).read_bytes().replace(b'__CSRF__', token.encode())
                mime = {'index.html':'text/html', 'app.js':'text/javascript', 'style.css':'text/css'}[name]
                self.reply(200, content, mime + '; charset=utf-8')
            elif parsed.path == '/api/state':
                self.reply(200, ledger.state())
            elif parsed.path == '/api/export.json':
                self.reply(200, json.dumps(ledger.state(), ensure_ascii=False, indent=2).encode(), filename='lifeos-expenses-backup.json')
            elif parsed.path == '/api/reminder.ics':
                identifier = parse_qs(parsed.query).get('id', [''])[0]
                state = ledger.state()
                entry = next((e for e in state['entries'] if e['id'] == identifier and e['dueAt']), None)
                if entry:
                    self.reply(200, ical(entry), 'text/calendar; charset=utf-8', 'lifeos-reminder.ics')
                else:
                    self.reply(404, {'error':'Reminder not found.'})
            else:
                self.reply(404, {'error':'Not found.'})

        def do_POST(self):
            if not self.allowed():
                return
            if not secrets.compare_digest(self.headers.get('X-LifeOS-Token', ''), token):
                self.reply(403, {'error': 'Refresh the page and try again.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Request is too large or empty.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Invalid request.')
                methods = {'/api/entries': ledger.save_entry, '/api/repayments': ledger.repay, '/api/reminders': ledger.toggle_reminder}
                action = methods.get(self.path)
                if not action:
                    self.reply(404, {'error':'Not found.'})
                    return
                self.reply(200, action(data))
            except (ValueError, TypeError, OverflowError) as error:
                self.reply(400, {'error': str(error) or 'Check your entry and try again.'})
            except (OSError, sqlite3.Error):
                self.reply(500, {'error':'Could not save. Check available disk space and retry.'})

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'LifeOS Pocket ready at http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == '__main__':
    main()

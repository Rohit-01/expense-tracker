#!/usr/bin/env python3
"""LifeOS Pocket: a loopback-only expense and loan ledger. No dependencies."""
import argparse
import calendar
import datetime as dt
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
import re
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


def page_destination(path):
    """Exact local page allowlist, shared by both servers and login returns."""
    if isinstance(path, str) and (path in ('/', '/quick-entry', '/trips') or
                                 re.fullmatch(r'/trips/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', path)):
        return path
    return '/'


def trip_page(page, path):
    if path == '/trips':
        return page.replace('<body>', '<body data-pocket-view="trips">')
    if path.startswith('/trips/'):
        return page.replace('<body>', f'<body data-pocket-view="trip" data-trip-id="{path.split("/")[-1]}">')
    return page


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def today():
    return dt.datetime.now(TZ).date()


def month_key(value):
    return value.strftime('%Y-%m')


def month_range(first, last):
    year, month = map(int, first.split('-'))
    while (year, month) <= tuple(map(int, last.split('-'))):
        yield f'{year:04d}-{month:02d}'
        month += 1
        if month == 13:
            year, month = year + 1, 1


def mandate_due(period, day):
    year, month = map(int, period.split('-'))
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1])).isoformat()


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
    rows = conn.execute('SELECT parent,name FROM categories ORDER BY rowid').fetchall()
    categories = [{'name': name, 'subcategories': [child for parent, child in rows if parent == name]}
                  for parent, name in rows if parent == '']
    by_id = {entry['id']: entry for entry in entries}
    payments = {(r[0], r[1]): {'expenseId': r[2], 'paidAt': r[3],
                              'title': by_id[r[2]]['title'], 'amountMinor': by_id[r[2]]['amountMinor'],
                              'category': by_id[r[2]]['category'], 'subcategory': by_id[r[2]].get('subcategory', '')}
                for r in conn.execute('SELECT mandate_id,period,expense_id,paid_at FROM mandate_payments')}
    mandates = []
    current = month_key(today())
    for row in conn.execute('SELECT payload FROM mandates ORDER BY rowid DESC'):
        mandate = json.loads(row[0])
        first = mandate['firstDueDate'][:7]
        occurrences = []
        for period in month_range(first, current) if first <= current else []:
            payment = payments.get((mandate['id'], period))
            visible = any(window['from'] <= period and (window['to'] is None or period <= window['to'])
                          for window in mandate['activeWindows'])
            if not visible and not payment:
                continue
            occurrences.append({'period': period, 'dueDate': mandate_due(period, mandate['dueDay']),
                                'status': 'paid' if payment else 'unpaid',
                                'expenseId': payment['expenseId'] if payment else None,
                                'paidAt': payment['paidAt'] if payment else None,
                                'paidTitle': payment['title'] if payment else None,
                                'paidAmountMinor': payment['amountMinor'] if payment else None,
                                'paidCategory': payment['category'] if payment else None,
                                'paidSubcategory': payment['subcategory'] if payment else None})
        mandates.append({**mandate, 'occurrences': list(reversed(occurrences))})
    trips = [json.loads(row[0]) for row in conn.execute('SELECT payload FROM trips ORDER BY rowid DESC')]
    active = conn.execute('SELECT value FROM settings WHERE key="activeTripId"').fetchone()
    return {'schemaVersion': 3, 'currency': 'INR', 'timezone': 'Asia/Kolkata',
            'entries': entries, 'repayments': repayments, 'categories': categories, 'mandates': mandates,
            'trips': trips, 'activeTripId': active[0] if active else None}


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
            conn.execute('CREATE TABLE IF NOT EXISTS categories (parent TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(parent,name))')
            conn.execute('CREATE TABLE IF NOT EXISTS mandates (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS trips (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS mandate_payments (mandate_id TEXT NOT NULL REFERENCES mandates(id), period TEXT NOT NULL, expense_id TEXT NOT NULL UNIQUE REFERENCES entries(id), paid_at TEXT NOT NULL, PRIMARY KEY(mandate_id,period))')
            for name in CATEGORIES:
                if not conn.execute('SELECT 1 FROM categories WHERE parent="" AND name=? COLLATE NOCASE', (name,)).fetchone():
                    conn.execute('INSERT INTO categories(parent,name) VALUES (?,?)', ('', name))
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

    def category_pair(self, conn, category, subcategory=''):
        if not isinstance(category, str) or not isinstance(subcategory, str):
            raise ValueError('Choose a valid category and subcategory.')
        row = conn.execute('SELECT name FROM categories WHERE parent="" AND name=? COLLATE NOCASE', (category.strip(),)).fetchone()
        if not row:
            raise ValueError('Choose a valid category.')
        category = row[0]
        if subcategory.strip():
            row = conn.execute('SELECT name FROM categories WHERE parent=? AND name=? COLLATE NOCASE', (category, subcategory.strip())).fetchone()
            if not row:
                raise ValueError('Choose a valid subcategory.')
            subcategory = row[0]
        return category, subcategory.strip()

    def trip(self, conn, identifier):
        if not isinstance(identifier, str):
            raise ValueError('Choose a valid trip.')
        row = conn.execute('SELECT payload FROM trips WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Trip not found.')
        return json.loads(row[0])

    def save_trip(self, data):
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = self.trip(conn, data['id']) if data.get('id') else None
            start = date(data['startDate']) if data.get('startDate') else ''
            end = date(data['endDate']) if data.get('endDate') else ''
            if start and end and end < start:
                raise ValueError('The end date must be on or after the start date.')
            trip = {'id': old['id'] if old else str(uuid.uuid4()),
                    'name': clean(data.get('name', ''), 100, True),
                    'destination': clean(data.get('destination', ''), 200),
                    'startDate': start, 'endDate': end,
                    'completed': old['completed'] if old else False,
                    'createdAt': old['createdAt'] if old else now(), 'updatedAt': now()}
            conn.execute('INSERT INTO trips(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (trip['id'], json.dumps(trip)))
        self.sync_note()
        return self.state()

    def trip_status(self, data):
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            trip = self.trip(conn, data.get('id'))
            action = data.get('action')
            if action == 'activate':
                if trip['completed']:
                    raise ValueError('Reopen this trip before making it active.')
                conn.execute('INSERT INTO settings(key,value) VALUES ("activeTripId",?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (trip['id'],))
            elif action in ('deactivate', 'complete', 'reopen'):
                if action != 'reopen':
                    conn.execute('DELETE FROM settings WHERE key="activeTripId" AND value=?', (trip['id'],))
                if action != 'deactivate':
                    trip.update(completed=action == 'complete', updatedAt=now())
                    conn.execute('UPDATE trips SET payload=? WHERE id=?', (json.dumps(trip), trip['id']))
            else:
                raise ValueError('Choose a valid trip action.')
        self.sync_note()
        return self.state()

    def save_category(self, data):
        name = clean(data.get('name', ''), 60, True)
        parent = clean(data.get('parent', ''), 60)
        if not any(char.isalnum() for char in name):
            raise ValueError('Use letters or numbers in the name.')
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if parent:
                parent, _ = self.category_pair(conn, parent)
            if conn.execute('SELECT 1 FROM categories WHERE parent=? AND name=? COLLATE NOCASE', (parent, name)).fetchone():
                raise ValueError('This category already exists.')
            conn.execute('INSERT INTO categories(parent,name) VALUES (?,?)', (parent, name))
        self.sync_note()
        return self.state()

    def save_mandate(self, data):
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = None
            if data.get('id'):
                row = conn.execute('SELECT payload FROM mandates WHERE id=?', (data['id'],)).fetchone()
                if not row:
                    raise ValueError('Mandate not found.')
                old = json.loads(row[0])
            title = clean(data.get('title', ''), 200, True)
            amount = money(data.get('amount'))
            first_due = date(data.get('firstDueDate'))
            category, subcategory = self.category_pair(conn, data.get('category', 'Other'), data.get('subcategory', ''))
            if old and first_due != old['firstDueDate']:
                raise ValueError('The first due date cannot change after creation.')
            mandate = {'id': old['id'] if old else str(uuid.uuid4()), 'title': title,
                       'amountMinor': amount, 'firstDueDate': first_due,
                       'dueDay': int(first_due[-2:]), 'category': category, 'subcategory': subcategory,
                       'activeWindows': old['activeWindows'] if old else [{'from': first_due[:7], 'to': None}],
                       'createdAt': old['createdAt'] if old else now(), 'updatedAt': now()}
            conn.execute('INSERT INTO mandates(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                         (mandate['id'], json.dumps(mandate)))
        self.sync_note()
        return self.state()

    def toggle_mandate(self, data):
        if type(data.get('paused')) is not bool:
            raise ValueError('Choose whether to pause this mandate.')
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM mandates WHERE id=?', (data.get('id'),)).fetchone()
            if not row:
                raise ValueError('Mandate not found.')
            mandate = json.loads(row[0]); windows = mandate['activeWindows']
            active = windows[-1]['to'] is None
            if data['paused'] and active:
                current = today(); previous = (current.replace(day=1) - dt.timedelta(days=1)).strftime('%Y-%m')
                windows[-1]['to'] = previous
            elif not data['paused'] and not active:
                windows.append({'from': month_key(today()), 'to': None})
            mandate['updatedAt'] = now()
            conn.execute('UPDATE mandates SET payload=? WHERE id=?', (json.dumps(mandate), mandate['id']))
        self.sync_note()
        return self.state()

    def pay_mandate(self, data):
        identifier, period = data.get('id'), data.get('period')
        if not isinstance(period, str) or len(period) != 7 or period[4] != '-' or not period[:4].isdigit() or not period[5:].isdigit() or not 1 <= int(period[5:]) <= 12:
            raise ValueError('Choose a valid mandate month.')
        with self.lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM mandates WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise ValueError('Mandate not found.')
            mandate = json.loads(row[0])
            if conn.execute('SELECT 1 FROM mandate_payments WHERE mandate_id=? AND period=?', (identifier, period)).fetchone():
                return self.state()
            if period < mandate['firstDueDate'][:7] or period > month_key(today()) or not any(
                    w['from'] <= period and (w['to'] is None or period <= w['to']) for w in mandate['activeWindows']):
                raise ValueError('This month is not available for payment.')
            timestamp = now()
            entry = {'id': str(uuid.uuid4()), 'kind': 'expense', 'title': mandate['title'],
                     'amountMinor': mandate['amountMinor'], 'person': '', 'category': mandate['category'],
                     'subcategory': mandate['subcategory'], 'date': today().isoformat(), 'dueAt': '',
                     'notes': '', 'reminderDone': False, 'createdAt': timestamp, 'updatedAt': timestamp,
                     'mandateId': identifier, 'mandateMonth': period}
            conn.execute('INSERT INTO entries(id,payload) VALUES (?,?)', (entry['id'], json.dumps(entry)))
            conn.execute('INSERT INTO mandate_payments(mandate_id,period,expense_id,paid_at) VALUES (?,?,?,?)',
                         (identifier, period, entry['id'], timestamp))
        self.sync_note()
        return self.state()

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
            category, subcategory = self.category_pair(conn, data.get('category', 'Other'), data.get('subcategory', ''))
            if old and old.get('mandateId') and kind != 'expense':
                raise ValueError('Mandate payments must remain expenses.')
            # Omitted by older clients: preserve edits, leave new entries unassigned.
            trip_id = data.get('tripId', old.get('tripId') if old else None)
            if trip_id not in (None, ''):
                self.trip(conn, trip_id)
            else:
                trip_id = None
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
                'subcategory': subcategory if kind == 'expense' else '',
                'date': date(data.get('date')), 'dueAt': reminder,
                'notes': clean(data.get('notes', ''), 2000),
                'tripId': trip_id,
                'reminderDone': old['reminderDone'] if old and old['dueAt'] == reminder and old['kind'] == kind else False,
                'createdAt': old['createdAt'] if old else now(), 'updatedAt': now(),
            }
            if old and old.get('mandateId'):
                entry['mandateId'] = old['mandateId']
                entry['mandateMonth'] = old['mandateMonth']
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
                trip_names = {t['id']: t['name'] for t in state['trips']}
                def trip_label(entry):
                    return md(trip_names.get(entry.get('tripId'), ''))
                expenses = [e for e in state['entries'] if e['kind'] == 'expense']
                loans = [e for e in state['entries'] if e['kind'] == 'loan']
                active = [e for e in state['entries'] if e['dueAt'] and not e['reminderDone'] and (e['kind'] != 'loan' or remaining(e, state['repayments']) > 0)]
                lines = [MARKER, '# Expense Ledger', '', f'Updated: {now()}', '', 'Generated from LifeOS Pocket. Edit entries in the tracker; this note is refreshed after every change.', '', 'Currency: INR · Reminder timezone: Asia/Kolkata', '', f'- Total expenses: {rupees(sum(e["amountMinor"] for e in expenses))}', f'- Outstanding loans: {rupees(sum(remaining(e, state["repayments"]) for e in loans))}', f'- Open reminders: {len(active)}', '', '## Expenses', '', '| Date | Item | Category | Subcategory | Amount | Mandate month | Notes | Trip |', '| --- | --- | --- | --- | ---: | --- | --- | --- |']
                for e in expenses:
                    lines.append(f'| {e["date"]} | {md(e["title"])} | {md(e["category"])} | {md(e.get("subcategory", ""))} | {rupees(e["amountMinor"])} | {e.get("mandateMonth", "")} | {md(e["notes"])} | {trip_label(e)} |')
                lines += ['', '## Mandates', '', '| Mandate | Month | Due | Amount | Category | Status |', '| --- | --- | --- | ---: | --- | --- |']
                for mandate in state['mandates']:
                    for occurrence in mandate['occurrences']:
                        paid = occurrence['status'] == 'paid'
                        category = occurrence['paidCategory'] if paid else mandate['category']
                        subcategory = occurrence['paidSubcategory'] if paid else mandate['subcategory']
                        label = category + (' → ' + subcategory if subcategory else '')
                        lines.append(f'| {md(occurrence["paidTitle"] if paid else mandate["title"])} | {occurrence["period"]} | {occurrence["dueDate"]} | {rupees(occurrence["paidAmountMinor"] if paid else mandate["amountMinor"])} | {md(label)} | {"Paid" if paid else "Unpaid"} |')
                lines += ['', '## Money lent', '', '| Date | Person | Purpose | Lent | Outstanding | Reminder | Notes | Trip |', '| --- | --- | --- | ---: | ---: | --- | --- | --- |']
                for e in loans:
                    lines.append(f'| {e["date"]} | {md(e["person"])} | {md(e["title"])} | {rupees(e["amountMinor"])} | {rupees(remaining(e,state["repayments"]))} | {e["dueAt"].replace("T"," ") or "Not set"} | {md(e["notes"])} | {trip_label(e)} |')
                lines += ['', '## Repayments received', '', '| Date | Person | Amount | Notes | Trip |', '| --- | --- | ---: | --- | --- |']
                for r in state['repayments']:
                    e = next(e for e in loans if e['id'] == r['entryId'])
                    lines.append(f'| {r["date"]} | {md(e["person"])} | {rupees(r["amountMinor"])} | {md(r["notes"])} | {trip_label(e)} |')
                lines += ['', '## Reminders', '', 'Reminder dates appear in the app. Calendar alerts require importing the downloaded calendar event.', '']
                for e in sorted((e for e in state['entries'] if e['dueAt']), key=lambda e:e['dueAt']):
                    settled = e['kind'] == 'loan' and remaining(e, state['repayments']) == 0
                    done = e['reminderDone'] or settled
                    label = 'Loan settled' if settled else ('Completed by user' if e['reminderDone'] else 'Open')
                    lines += [f'- [{"x" if done else " "}] {md(e["title"])}', f'  - Due: {e["dueAt"].replace("T"," ")} IST', f'  - Status: {label}', f'  - Trip: {trip_label(e) or "No trip"}', f'  - Notes: {md(e["notes"]) or "—"}']
                lines += ['', '## Trips', '', 'Trip totals include all linked dates. These are the same records shown above, not additional spending.', '', '| Trip | Destination | Dates | Status | Spending | To collect | Open reminders |', '| --- | --- | --- | --- | ---: | ---: | ---: |']
                for trip in state['trips']:
                    identifier = trip['id']
                    status = 'Completed' if trip['completed'] else 'Active' if state['activeTripId'] == identifier else 'Open'
                    dates = ' → '.join([trip['startDate'] or 'Not set', trip['endDate'] or 'Not set'])
                    spent = sum(e['amountMinor'] for e in expenses if e.get('tripId') == identifier)
                    owed = sum(remaining(e, state['repayments']) for e in loans if e.get('tripId') == identifier)
                    reminders = sum(1 for e in active if e.get('tripId') == identifier)
                    lines.append(f'| {md(trip["name"])} | {md(trip["destination"])} | {dates} | {status} | {rupees(spent)} | {rupees(owed)} | {reminders} |')
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
            if (page_destination(parsed.path) == parsed.path or parsed.path in ['/app.js', '/quick_parse.js', '/journal.js', '/install.js', '/style.css', '/manifest.webmanifest', '/favicon.ico',
                               '/icons/pocket-192.png', '/icons/pocket-512.png',
                               '/icons/apple-touch-icon.png', '/icons/favicon-32.png', '/companion.js']):
                if parsed.path.startswith('/trips/') and not any(t['id'] == parsed.path.split('/')[-1] for t in ledger.state()['trips']):
                    self.reply(404, {'error': 'Trip not found.'})
                    return
                name = 'index.html' if page_destination(parsed.path) == parsed.path else parsed.path[1:]
                content = (ROOT / name).read_bytes()
                if name == 'index.html':
                    page = trip_page(content.decode().replace('__CSRF__', token), parsed.path)
                    if parsed.path == '/quick-entry':
                        page = page.replace('<body>', '<body data-pocket-view="quick-entry">').replace('</head>', '<script src="/companion.js" defer></script></head>')
                    content = page.encode()
                mime = ('image/x-icon' if name == 'favicon.ico' else
                        'image/png' if name.startswith('icons/') else
                        'application/manifest+json; charset=utf-8' if name == 'manifest.webmanifest' else
                        {'index.html':'text/html', 'app.js':'text/javascript', 'quick_parse.js':'text/javascript', 'journal.js':'text/javascript', 'install.js':'text/javascript', 'companion.js':'text/javascript', 'style.css':'text/css'}[name] + '; charset=utf-8')
                self.reply(200, content, mime)
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
                methods = {'/api/entries': ledger.save_entry, '/api/repayments': ledger.repay,
                           '/api/reminders': ledger.toggle_reminder, '/api/categories': ledger.save_category,
                           '/api/mandates': ledger.save_mandate, '/api/mandate-status': ledger.toggle_mandate,
                           '/api/mandate-payments': ledger.pay_mandate,
                           '/api/trips': ledger.save_trip, '/api/trip-status': ledger.trip_status}
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

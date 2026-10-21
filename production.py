"""Production HTTP adapter. Run only behind HTTPS using deploy/pocket.service."""
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from server import Ledger, ROOT, ical


def create_app(settings=None):
    settings = settings or os.environ
    domain = settings.get('POCKET_DOMAIN', '').lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', domain) or '.' not in domain:
        raise RuntimeError('Set POCKET_DOMAIN to your domain, without https:// or a path.')
    data_dir = Path(settings.get('POCKET_DATA_DIR', '/var/lib/pocket/data'))
    auth_file = Path(settings.get('POCKET_AUTH_FILE', '/var/lib/pocket/auth.json'))
    try:
        auth = json.loads(auth_file.read_text())
        username, password_hash = auth['username'], auth['passwordHash']
        if not username or not password_hash.startswith('scrypt:'):
            raise ValueError()
    except (OSError, KeyError, ValueError, TypeError):
        raise RuntimeError('Create the login first with deploy/create_login.py.') from None

    ledger = Ledger(data_dir, settings.get('POCKET_VAULT', '/var/lib/pocket/vault-export'))
    token = secrets.token_urlsafe(32)
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=16384, TRUSTED_HOSTS=[domain])
    # One trusted proxy (Caddy); Gunicorn is bound only to 127.0.0.1.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    failed_logins = {}
    login_lock = threading.Lock()

    def state():
        result = ledger.state()
        result['storageLocation'] = 'vps'
        result['sync'] = {**result['sync'], 'remoteExport': True}
        if result['sync']['ok']:
            result['sync']['message'] = 'Ledger exported on VPS; Mac sync is separate.'
        return result

    @app.before_request
    def protect():
        # Health is deliberately data-free. Every other route requires login.
        if request.path == '/healthz' and request.method in ['GET', 'HEAD']:
            return None
        if not request.is_secure:
            return jsonify(error='Use the HTTPS address for Pocket.'), 403
        address = request.remote_addr or 'unknown'
        timestamp = time.monotonic()
        with login_lock:
            expired = [key for key, (_, since) in failed_logins.items() if timestamp - since >= 300]
            for key in expired:
                failed_logins.pop(key, None)
            failures, since = failed_logins.get(address, (0, timestamp))
            if failures >= 10:
                return jsonify(error='Too many failed sign-in attempts. Try again in five minutes.'), 429, {'Retry-After':'300'}
        credentials = request.authorization
        valid = False
        if credentials and credentials.type == 'basic' and credentials.username and credentials.password:
            # Always verify the password to avoid revealing whether a username exists.
            valid_password = check_password_hash(password_hash, credentials.password)
            valid = secrets.compare_digest(credentials.username.encode(), username.encode()) and valid_password
        if not valid:
            if credentials:
                with login_lock:
                    # Bound memory even if exposed to a very large number of clients.
                    if len(failed_logins) >= 4096 and address not in failed_logins:
                        return jsonify(error='Sign-in temporarily busy. Try again later.'), 429
                    count, since = failed_logins.get(address, (0, timestamp))
                    failed_logins[address] = (count + 1, since)
            return jsonify(error='Sign in to Pocket.'), 401, {'WWW-Authenticate':'Basic realm="Pocket", charset="UTF-8"'}
        with login_lock:
            failed_logins.pop(address, None)
        if request.method not in ['GET', 'HEAD', 'OPTIONS']:
            if request.headers.get('Origin') != 'https://' + domain:
                return jsonify(error='This request must come from your Pocket page.'), 403
            supplied = request.headers.get('X-LifeOS-Token', '').encode()
            if not secrets.compare_digest(supplied, token.encode()):
                return jsonify(error='Refresh the page and try again.'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    def index():
        page = (ROOT/'index.html').read_text().replace('__CSRF__', token)
        page = page.replace('Saved on this Mac. Your journal stays with you.', 'Saved on your VPS. Your journal stays with you.')
        return Response(page, content_type='text/html; charset=utf-8')

    @app.get('/app.js')
    def script():
        return Response((ROOT/'app.js').read_bytes(), content_type='text/javascript; charset=utf-8')

    @app.get('/style.css')
    def styles():
        return Response((ROOT/'style.css').read_bytes(), content_type='text/css; charset=utf-8')

    @app.get('/api/state')
    def get_state():
        return jsonify(state())

    @app.get('/api/export.json')
    def export():
        return Response(json.dumps(state(), ensure_ascii=False, indent=2), content_type='application/json; charset=utf-8', headers={'Content-Disposition':'attachment; filename="lifeos-expenses-backup.json"'})

    @app.get('/api/ledger.md')
    def export_markdown():
        # Authenticated export of a fresh snapshot; never serves arbitrary files.
        with ledger.lock:
            ledger.sync_note()
            if not ledger.sync['ok']:
                return jsonify(error='Ledger export unavailable. Your database remains saved.'), 503
            content = (ledger.vault/'ai zone/openai agent/Personal/Finance/Expense Ledger.md').read_bytes()
        return Response(content, content_type='text/markdown; charset=utf-8', headers={'Content-Disposition':'attachment; filename="Expense Ledger.md"'})

    @app.get('/api/reminder.ics')
    def calendar():
        entry = next((e for e in ledger.state()['entries'] if e['id'] == request.args.get('id') and e['dueAt']), None)
        if not entry:
            return jsonify(error='Reminder not found.'), 404
        return Response(ical(entry), content_type='text/calendar; charset=utf-8', headers={'Content-Disposition':'attachment; filename="lifeos-reminder.ics"'})

    @app.post('/api/<action>')
    def mutate(action):
        methods = {'entries':ledger.save_entry, 'repayments':ledger.repay, 'reminders':ledger.toggle_reminder}
        if action not in methods:
            return jsonify(error='Not found.'), 404
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify(error='Invalid request.'), 400
        try:
            methods[action](data)
            return jsonify(state())
        except (ValueError, TypeError, OverflowError):
            return jsonify(error='Check the required fields, dates, and amounts. Repayments cannot exceed the amount outstanding.'), 400
        except (OSError, sqlite3.Error):
            return jsonify(error='Could not save. Check server storage and try again.'), 500

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name), error.code

    return app

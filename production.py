"""Production adapter: one Gunicorn worker behind a trusted loopback HTTPS proxy."""
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading

from flask import Flask, Response, g, jsonify, redirect, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from accounts import Accounts, AuthError, COOKIE, account_directory
from server import Ledger, ROOT, ical


def create_app(settings=None):
    settings = settings if settings is not None else os.environ
    domain = settings.get('POCKET_DOMAIN', '').lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', domain) or '.' not in domain:
        raise RuntimeError('Set POCKET_DOMAIN to your domain, without https:// or a path.')
    # Databases, journals, and account exports must not be readable by other OS users.
    os.umask(0o077)
    data_dir = Path(settings.get('POCKET_DATA_DIR', '/var/lib/pocket/data')).expanduser().resolve()
    configured_vault = settings.get('POCKET_VAULT', '/var/lib/pocket/vault-export')
    owner_vault = Path(configured_vault).expanduser().resolve() if configured_vault else None
    users_root = data_dir / 'accounts'
    if owner_vault and (owner_vault == users_root or users_root in owner_vault.parents or owner_vault in users_root.parents):
        raise RuntimeError('POCKET_VAULT and the private accounts directory must be separate trees.')
    accounts = Accounts(data_dir, settings.get('POCKET_AUTH_FILE', '/var/lib/pocket/auth.json'))
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=16384, TRUSTED_HOSTS=[domain])
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.extensions['pocket_accounts'] = accounts
    ledgers, ledger_lock = {}, threading.RLock()

    def get_ledger(user):
        # The only route to owner_vault is the server-assigned owner flag.
        with ledger_lock:
            if user['id'] not in ledgers:
                directory = account_directory(data_dir, user)
                vault = owner_vault if user['is_owner'] else directory / 'export'
                ledgers[user['id']] = Ledger(directory, str(vault) if vault else '')
            return ledgers[user['id']]

    def state():
        result = get_ledger(g.session).state()
        result['storageLocation'] = 'vps'
        result['account'] = {'id': g.session['id'], 'username': g.session['username'], 'isOwner': bool(g.session['is_owner'])}
        result['sync'] = {**result['sync'], 'remoteExport': True, 'ownerExport': bool(g.session['is_owner'])}
        # Do not disclose filesystem paths or local exception details in the response.
        result['sync']['message'] = ('Your ledger is exported on the VPS. Mac sync is separate.' if g.session['is_owner'] else 'Your private ledger is exported on the VPS.') if result['sync']['ok'] else 'Records saved. Ledger export needs administrator attention.'
        return result

    @app.before_request
    def protect():
        # Force host validation before an authentication response can short-circuit routing.
        if request.host != domain:
            return jsonify(error='Invalid host.'), 400
        if request.path == '/healthz' and request.method in ['GET', 'HEAD']:
            return None
        if not request.is_secure:
            return jsonify(error='Use the HTTPS address for Pocket.'), 403
        # This one read-only endpoint uses a bearer token, never browser cookies.
        # All other routes retain the existing session and CSRF requirements.
        if request.path == '/api/obsidian/ledger.md':
            if request.method not in ['GET', 'HEAD']:
                return jsonify(error='Read-only endpoint.'), 405
            credential = re.fullmatch(r'Bearer (pocket_sync_[A-Za-z0-9_-]{43})', request.headers.get('Authorization', ''), re.IGNORECASE)
            g.sync_owner = accounts.sync_owner(credential[1]) if credential else None
            if not g.sync_owner:
                return jsonify(error='A valid owner sync token is required.'), 401
            if request.query_string:
                return jsonify(error='This endpoint does not accept query parameters.'), 400
            return None
        g.session = accounts.session(request.cookies.get(COOKIE))
        public = request.path in {'/login', '/signup', '/auth.js', '/style.css',
                                  '/manifest.webmanifest', '/icons/pocket-192.png',
                                  '/icons/pocket-512.png', '/icons/apple-touch-icon.png'}
        if not public and not (g.session and g.session['user_id']):
            if request.path == '/':
                return redirect('/login', code=303)
            return jsonify(error='Sign in to Pocket.'), 401
        if request.method not in ['GET', 'HEAD', 'OPTIONS']:
            if request.headers.get('Origin') != 'https://' + domain:
                return jsonify(error='This request must come from your Pocket page.'), 403
            supplied = request.headers.get('X-LifeOS-Token', '')
            if not g.session or not secrets.compare_digest(supplied.encode(), g.session['csrf'].encode()):
                return jsonify(error='Your session changed or expired. Refresh the page and try again.'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def cookie_response(response, issued):
        token, _, seconds = issued
        response.set_cookie(COOKIE, token, max_age=seconds, secure=True, httponly=True, samesite='Lax', path='/')
        return response

    def json_body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise AuthError('Invalid request.')
        return data

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.route('/login', methods=['GET', 'POST'])
    @app.route('/signup', methods=['GET', 'POST'])
    def authenticate():
        signup = request.path == '/signup'
        if g.session and g.session['user_id']:
            return redirect('/', code=303) if request.method == 'GET' else (jsonify(error='Log out before using another account.'), 409)
        if request.method == 'GET':
            issued = None
            if not g.session:
                issued = accounts.anonymous(request.cookies.get(COOKIE))
                csrf = issued[1]
            else:
                csrf = g.session['csrf']
            response = Response(render_template('auth.html', signup=signup, csrf=csrf), content_type='text/html; charset=utf-8')
            return cookie_response(response, issued) if issued else response
        data = json_body()
        address, old_token = request.remote_addr or 'unknown', request.cookies.get(COOKIE)
        if signup:
            issued = accounts.register(data.get('username'), data.get('password'), data.get('confirmation'), address, old_token, get_ledger)
        else:
            issued = accounts.login(data.get('username'), data.get('password'), address, old_token)
        return cookie_response(jsonify(redirect='/'), issued)

    @app.post('/logout')
    def logout():
        accounts.revoke(request.cookies[COOKIE])
        response = jsonify(redirect='/login')
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='Lax', path='/')
        return response

    @app.get('/')
    def index():
        # A per-session token also prevents stale tabs from writing into a new account.
        page = (ROOT/'index.html').read_text().replace('__CSRF__', g.session['csrf'])
        page = page.replace('</head>', '<meta name="pocket-account" content="' + g.session['id'] + '"></head>')
        page = page.replace('Saved on this Mac. Your journal stays with you.', 'Saved in your private account on the VPS.')
        return Response(page, content_type='text/html; charset=utf-8')

    @app.get('/app.js')
    @app.get('/auth.js')
    @app.get('/style.css')
    @app.get('/manifest.webmanifest')
    @app.get('/icons/pocket-192.png')
    @app.get('/icons/pocket-512.png')
    @app.get('/icons/apple-touch-icon.png')
    def asset():
        mime = ('text/css; charset=utf-8' if request.path == '/style.css' else
                'application/manifest+json; charset=utf-8' if request.path == '/manifest.webmanifest' else
                'image/png' if request.path.startswith('/icons/') else 'text/javascript; charset=utf-8')
        return Response((ROOT/request.path[1:]).read_bytes(), content_type=mime)

    @app.get('/api/state')
    def get_state():
        return jsonify(state())

    @app.get('/api/export.json')
    def export():
        return Response(json.dumps(state(), ensure_ascii=False, indent=2), content_type='application/json; charset=utf-8', headers={'Content-Disposition':'attachment; filename="lifeos-expenses-backup.json"'})

    @app.get('/api/ledger.md')
    def export_markdown():
        return ledger_response(g.session)

    def ledger_response(user):
        ledger = get_ledger(user)
        with ledger.lock:
            ledger.sync_note()
            if not ledger.sync['ok']:
                return jsonify(error='Ledger export unavailable. Your database remains saved.'), 503
            content = (ledger.vault/'ai zone/openai agent/Personal/Finance/Expense Ledger.md').read_bytes()
        return Response(content, content_type='text/markdown; charset=utf-8', headers={'Content-Disposition':'attachment; filename="Expense Ledger.md"'})

    @app.get('/api/obsidian/ledger.md')
    def obsidian_ledger():
        response = ledger_response(g.sync_owner)
        if isinstance(response, Response):
            response.headers['X-Pocket-Owner-ID'] = g.sync_owner['id']
        return response

    def sync_details(details):
        return jsonify(**details, ownerId=g.session['id'], endpoint='https://' + domain + '/api/obsidian/ledger.md')

    @app.get('/api/obsidian/token')
    def sync_token_status():
        return sync_details(accounts.sync_status(g.session['id']))

    @app.post('/api/obsidian/token')
    def issue_sync_token():
        return sync_details(accounts.create_sync_token(g.session['id']))

    @app.post('/api/obsidian/token/revoke')
    def revoke_sync_token():
        accounts.revoke_sync_token(g.session['id'])
        return sync_details(accounts.sync_status(g.session['id']))

    @app.get('/api/reminder.ics')
    def calendar():
        entry = next((e for e in get_ledger(g.session).state()['entries'] if e['id'] == request.args.get('id') and e['dueAt']), None)
        if not entry:
            return jsonify(error='Reminder not found.'), 404
        return Response(ical(entry), content_type='text/calendar; charset=utf-8', headers={'Content-Disposition':'attachment; filename="lifeos-reminder.ics"'})

    @app.post('/api/<action>')
    def mutate(action):
        ledger = get_ledger(g.session)
        methods = {'entries':ledger.save_entry, 'repayments':ledger.repay, 'reminders':ledger.toggle_reminder,
                   'categories':ledger.save_category, 'mandates':ledger.save_mandate,
                   'mandate-status':ledger.toggle_mandate, 'mandate-payments':ledger.pay_mandate}
        if action not in methods:
            return jsonify(error='Not found.'), 404
        data = json_body()
        try:
            methods[action](data)
            return jsonify(state())
        except ValueError as error:
            if action in {'categories', 'mandates', 'mandate-status', 'mandate-payments'}:
                return jsonify(error=str(error)), 400
            return jsonify(error='Check the required fields, dates, and amounts. Repayments cannot exceed the amount outstanding.'), 400
        except (TypeError, OverflowError):
            return jsonify(error='Check the required fields, dates, and amounts. Repayments cannot exceed the amount outstanding.'), 400

    @app.errorhandler(AuthError)
    def auth_error(error):
        return jsonify(error=str(error)), error.status, ({'Retry-After': str(error.retry)} if error.retry else {})

    @app.errorhandler(OSError)
    @app.errorhandler(sqlite3.Error)
    def storage_error(error):
        return jsonify(error='Could not complete this request. Check server storage and try again.'), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name), error.code

    # Initialize the legacy ledger only after an owner has been safely established.
    with accounts.connect() as conn:
        owner = dict(conn.execute('SELECT id, is_owner FROM users WHERE is_owner=1').fetchone())
    get_ledger(owner)
    return app

"""Persistent accounts, hashed session credentials, and transactional rate limits."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from werkzeug.security import check_password_hash, generate_password_hash

SESSION_SECONDS = 7 * 24 * 60 * 60
ANONYMOUS_SECONDS = 30 * 60
COOKIE = '__Host-pocket-session'


class AuthError(Exception):
    def __init__(self, message, status=400, retry=None):
        super().__init__(message)
        self.status, self.retry = status, retry


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_password(password):
    if not isinstance(password, str) or not 14 <= len(password) <= 1024:
        raise AuthError('Use a password between 14 and 1,024 characters.')


def account_directory(data_dir, user):
    # Only database-owned UUIDs may select a directory; never use a username/path input.
    identifier = str(uuid.UUID(user['id']))
    return Path(data_dir) if user['is_owner'] else Path(data_dir) / 'accounts' / identifier


class Accounts:
    def __init__(self, data_dir, auth_file):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / 'accounts.sqlite3'
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                    is_owner INTEGER NOT NULL CHECK(is_owner IN (0,1)), created_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON users(is_owner) WHERE is_owner=1;
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
                    csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS session_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS attempts (
                    kind TEXT NOT NULL, address TEXT NOT NULL, happened_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempt_lookup ON attempts(kind,address,happened_at);
            ''')
        os.chmod(self.db, 0o600)
        self.import_owner(auth_file)
        # Used for unknown usernames too, so login failures have similar password work.
        self.dummy_hash = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def import_owner(self, auth_file):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT id FROM users WHERE is_owner=1').fetchone():
                return
            if conn.execute('SELECT id FROM users LIMIT 1').fetchone():
                raise RuntimeError('Account database has no owner. Restore a verified backup before starting Pocket.')
            try:
                legacy = json.loads(Path(auth_file).read_text())
                name, password_hash = legacy['username'], legacy['passwordHash']
                if not isinstance(name, str) or not name.strip() or len(name) > 100:
                    raise ValueError()
                if not isinstance(password_hash, str) or not password_hash.startswith('scrypt:'):
                    raise ValueError()
                check_password_hash(password_hash, 'migration-format-check')
            except (OSError, KeyError, ValueError, TypeError):
                raise RuntimeError('Owner setup required: set POCKET_AUTH_FILE to your existing auth.json. For a new installation, use deploy/create_login.py first.') from None
            conn.execute('INSERT INTO users VALUES (?,?,?,?,1,?)',
                         (str(uuid.uuid4()), name, name.strip().casefold(), password_hash, int(time.time())))

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.connect() as conn:
            row = conn.execute('''SELECT sessions.*, users.id, users.username, users.is_owner
                FROM sessions LEFT JOIN users ON users.id=sessions.user_id
                WHERE token_hash=? AND expires_at>?''', (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def _issue(self, conn, old_token, user_id=None):
        now = int(time.time())
        conn.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
        if old_token:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest(old_token),))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        seconds = SESSION_SECONDS if user_id else ANONYMOUS_SECONDS
        conn.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token), user_id, csrf, now + seconds))
        return token, csrf, seconds

    def anonymous(self, old_token=None):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return self._issue(conn, old_token)

    def revoke(self, token):
        with self.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def _limit(self, conn, kind, address, window, maximum):
        now = int(time.time())
        conn.execute('DELETE FROM attempts WHERE happened_at<=?', (now - 3600,))
        # Store an address digest, never raw addresses alongside account data.
        address = digest(address)
        row = conn.execute('SELECT COUNT(*), MIN(happened_at) FROM attempts WHERE kind=? AND address=? AND happened_at>?',
                           (kind, address, now - window)).fetchone()
        if row[0] >= maximum:
            raise AuthError('Too many attempts. Please try again later.', 429, max(1, row[1] + window - now))
        return address, now

    def login(self, username, password, address, old_token):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 1024:
            raise AuthError('Enter a valid username and password.')
        failed = False
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            ip, now = self._limit(conn, 'login', address, 300, 10)
            user = conn.execute('SELECT * FROM users WHERE username_key=?', (username.strip().casefold(),)).fetchone()
            valid = check_password_hash(user['password_hash'] if user else self.dummy_hash, password)
            if not user or not valid:
                conn.execute('INSERT INTO attempts VALUES (?,?,?)', ('login', ip, now))
                failed = True
            else:
                issued = self._issue(conn, old_token, user['id'])
        # Raise after commit so failed attempts survive a restart.
        if failed:
            raise AuthError('Username or password is incorrect.', 401)
        return issued

    def register(self, username, password, confirmation, address, old_token, provision):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', username):
            raise AuthError('Use 3–32 letters, numbers, dots, underscores, or hyphens for your username.')
        validate_password(password)
        if password != confirmation:
            raise AuthError('Passwords do not match.')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            ip, now = self._limit(conn, 'signup', address, 3600, 5)
            if conn.execute('SELECT 1 FROM users WHERE username_key=?', (username.casefold(),)).fetchone():
                raise AuthError('That username is already taken. Choose another.', 409)
            user = {'id': str(uuid.uuid4()), 'username': username, 'is_owner': 0}
            password_hash = generate_password_hash(password, method='scrypt')
            # Create the empty ledger before committing ownership; backups can then include it.
            provision(user)
            conn.execute('INSERT INTO users VALUES (?,?,?,?,0,?)',
                         (user['id'], username, username.casefold(), password_hash, now))
            conn.execute('INSERT INTO attempts VALUES (?,?,?)', ('signup', ip, now))
            return self._issue(conn, old_token, user['id'])

    def reset_password(self, username, password):
        validate_password(password)
        password_hash = generate_password_hash(password, method='scrypt')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            user = conn.execute('SELECT id FROM users WHERE username_key=?', (username.strip().casefold(),)).fetchone()
            if not user:
                raise AuthError('Account not found. Nothing changed.')
            conn.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash, user['id']))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))

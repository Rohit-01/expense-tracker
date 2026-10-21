#!/usr/bin/env python3
"""Snapshot accounts and registered ledgers using SQLite's online backup API."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounts import account_directory


def copy_database(source, target):
    if not source.is_file():
        raise ValueError('A required database is missing; backup is incomplete.')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup failed its integrity check.')
    finally:
        src.close()
        dst.close()
    os.chmod(target, 0o600)


def make_backup(data_dir, directory):
    os.umask(0o077)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = directory / ('pocket-' + stamp)
    target.mkdir(parents=True, mode=0o700)
    files = []
    auth = data_dir / 'accounts.sqlite3'
    if auth.exists():
        copy_database(auth, target / 'accounts.sqlite3')
        files.append('accounts.sqlite3')
        with sqlite3.connect(target / 'accounts.sqlite3') as conn:
            conn.row_factory = sqlite3.Row
            users = conn.execute('SELECT id, is_owner FROM users').fetchall()
        if sum(user['is_owner'] for user in users) != 1:
            raise ValueError('Account database must contain exactly one owner.')
        for user in users:
            relative = (account_directory(data_dir, user) / 'ledger.sqlite3').relative_to(data_dir)
            copy_database(data_dir / relative, target / relative)
            files.append(str(relative))
    else:
        copy_database(data_dir / 'ledger.sqlite3', target / 'ledger.sqlite3')
        files.append('ledger.sqlite3')
    # Written last: its absence marks an incomplete backup, never a usable snapshot.
    (target / 'manifest.json').write_text(json.dumps({'version': 1, 'createdAt': stamp, 'files': files}, indent=2) + '\n')
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('POCKET_DATA_DIR', '/var/lib/pocket/data')))
    parser.add_argument('--directory', type=Path, default=Path('/var/lib/pocket/backups'))
    parser.add_argument('--source', type=Path, help='Legacy option: snapshot this single ledger only; excludes accounts.')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.source:
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            target = args.directory / ('ledger-' + stamp + '.sqlite3')
            copy_database(args.source, target)
            print('Single-ledger snapshot only; excludes accounts and other users.', file=sys.stderr)
        else:
            target = make_backup(args.data_dir, args.directory)
    except (OSError, ValueError, sqlite3.Error):
        raise SystemExit('Backup failed. Any partial snapshot without manifest.json must not be restored.') from None
    print(target)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Restore a complete snapshot into a NEW data directory; never overwrite live data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounts import account_directory
from deploy.backup import copy_database


def restore(snapshot, destination):
    if destination.exists():
        raise ValueError('Choose a new, nonexistent destination; existing data is never replaced.')
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    files = manifest.get('files')
    if manifest.get('version') != 1 or not isinstance(files, list) or 'ledger.sqlite3' not in files:
        raise ValueError('Unsupported or incomplete snapshot.')
    expected = {'ledger.sqlite3'}
    if 'accounts.sqlite3' in files:
        with sqlite3.connect((snapshot / 'accounts.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            users = conn.execute('SELECT id, is_owner FROM users').fetchall()
        if sum(user['is_owner'] for user in users) != 1:
            raise ValueError('Snapshot does not have exactly one owner.')
        expected.add('accounts.sqlite3')
        expected.update(str((account_directory(Path('.'), user) / 'ledger.sqlite3')) for user in users)
    if set(files) != expected or len(files) != len(expected):
        raise ValueError('Snapshot ownership mapping and files do not match.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.pocket-restore-', dir=destination.parent))
    try:
        for name in files:
            copy_database(snapshot / name, staging / name)
        if (staging / 'accounts.sqlite3').exists():
            with sqlite3.connect(staging / 'accounts.sqlite3') as conn:
                conn.execute('DELETE FROM sessions')
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        restored = restore(args.snapshot, args.data_dir)
    except (OSError, ValueError, sqlite3.Error, TypeError):
        raise SystemExit('Restore failed. Check the snapshot and use a new destination.') from None
    print('Restored to ' + str(restored) + '. Sessions revoked; notes regenerate when the app opens each ledger.')


if __name__ == '__main__':
    main()

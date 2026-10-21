#!/usr/bin/env python3
"""Make a consistent SQLite snapshot, even when Pocket is running."""
import argparse
import datetime as dt
import os
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, default=Path('/var/lib/pocket/data/ledger.sqlite3'))
parser.add_argument('--directory', type=Path, default=Path('/var/lib/pocket/backups'))
args = parser.parse_args()
if not args.source.is_file():
    raise SystemExit('Source database does not exist; no backup created.')
os.umask(0o077)
args.directory.mkdir(parents=True, exist_ok=True)
stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
target = args.directory/f'ledger-{stamp}.sqlite3'
source = sqlite3.connect(args.source.resolve().as_uri()+'?mode=ro', uri=True)
backup = sqlite3.connect(target)
try:
    source.backup(backup)
    if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('Backup failed its integrity check. Do not use it.')
finally:
    source.close()
    backup.close()
print(target)

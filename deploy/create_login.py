#!/usr/bin/env python3
"""Prompt locally on the VPS; never place a password in a command or log."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
from werkzeug.security import generate_password_hash

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=Path('/var/lib/pocket/auth.json'))
args = parser.parse_args()
if not sys.stdin.isatty():
    raise SystemExit('Run this interactively in your VPS terminal.')
username = input('Pocket username: ').strip()
if not username or ':' in username or len(username) > 100:
    raise SystemExit('Choose a username without a colon, up to 100 characters.')
password = getpass.getpass('Pocket password (at least 14 characters): ')
if len(password) < 14:
    raise SystemExit('Use at least 14 characters.')
if password != getpass.getpass('Repeat password: '):
    raise SystemExit('Passwords did not match. Nothing changed.')
if args.output.exists() and input('Replace the existing login? Type yes: ') != 'yes':
    raise SystemExit('Nothing changed.')
args.output.parent.mkdir(parents=True, exist_ok=True)
payload = json.dumps({'username':username, 'passwordHash':generate_password_hash(password)}, indent=2)+'\n'
temporary = args.output.with_suffix('.json.new')
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(payload)
temporary.replace(args.output)
print('Owner bootstrap saved. Start Pocket to import it once. After migration, use deploy/reset_password.py to change a password.')

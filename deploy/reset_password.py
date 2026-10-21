#!/usr/bin/env python3
"""Interactive administrator reset; never accept a password on the command line."""
import argparse
import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounts import Accounts, AuthError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('POCKET_DATA_DIR', '/var/lib/pocket/data')))
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('Run interactively in your VPS terminal.')
    if not (args.data_dir / 'accounts.sqlite3').is_file():
        raise SystemExit('Account database not found. Start the updated app once to migrate the owner first.')
    accounts = Accounts(args.data_dir, args.data_dir / 'unused-legacy-auth.json')
    username = input('Account username to reset: ').strip()
    password = getpass.getpass('New password (at least 14 characters): ')
    if password != getpass.getpass('Repeat new password: '):
        raise SystemExit('Passwords did not match. Nothing changed.')
    if input('Reset this password and sign out its sessions? Type yes: ') != 'yes':
        raise SystemExit('Nothing changed.')
    try:
        accounts.reset_password(username, password)
    except AuthError as error:
        raise SystemExit(str(error)) from None
    print('Password reset. All sessions for that account were revoked. No app restart is needed.')


if __name__ == '__main__':
    main()

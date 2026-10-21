#!/usr/bin/env python3
"""Fetch the owner's ledger over HTTPS using a token stored only in macOS Keychain."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request
import uuid
import warnings

GENERATED = '<!-- lifeos-pocket-generated:v1 -->'
REMOTE_COPY = '<!-- pocket-vps-sync:v1 -->'
NOTE = Path('ai zone/openai agent/Personal/Finance/Expense Ledger.md')
ENDPOINT = '/api/obsidian/ledger.md'
MAX_BYTES = 16 * 1024 * 1024
DEFAULT_CONFIG = Path('~/Library/Application Support/pocket-obsidian-sync/config.json').expanduser()


class SyncError(ValueError):
    """Only fixed, non-sensitive messages from this class may be logged."""


def origin_url(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.path not in ('', '/')
                or parts.query or parts.fragment or not re.fullmatch(r'[A-Za-z0-9.:-]+', parts.netloc)
                or (parts.port is not None and not 1 <= parts.port <= 65535)):
            raise ValueError()
        return 'https://' + parts.netloc.lower().removesuffix(':443')
    except (ValueError, TypeError):
        raise SyncError('Use the Pocket HTTPS address without a path, query, or credentials.') from None


def owner_id(value):
    try:
        result = str(uuid.UUID(value))
        if result != value:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise SyncError('The owner identity is missing or invalid. Existing note was preserved.') from None


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r'pocket_sync_[A-Za-z0-9_-]{43}', token):
        raise SyncError('No valid sync token found. Run setup with a token from Pocket.')
    return token


def markdown_text(payload):
    if not payload or len(payload) > MAX_BYTES:
        raise SyncError('Ledger is empty or too large. Existing note was preserved.')
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError:
        raise SyncError('Ledger is not UTF-8 Markdown. Existing note was preserved.') from None
    if not text.startswith(GENERATED + '\n') or '\n# Expense Ledger\n' not in text or '\x00' in text:
        raise SyncError('Source is not a Pocket-generated ledger. Existing note was preserved.')
    return text


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib otherwise forwards Authorization to redirected hosts.
        raise SyncError('Pocket redirected the request. Check its HTTPS address and proxy configuration.')


def fetch_owner_note(origin, token, expected_owner=None):
    url = origin_url(origin) + ENDPOINT
    token = validate_token(token)
    if expected_owner is not None:
        owner_id(expected_owner)
    request = Request(url, headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'text/markdown', 'Accept-Encoding': 'identity',
                                   'User-Agent': 'pocket-obsidian-sync/1'})
    # No environmental proxies, redirects, cookies, or insecure TLS fallback.
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise SyncError('Unexpected Pocket response. Existing note was preserved.')
            if response.headers.get_content_type() != 'text/markdown' or response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise SyncError('Pocket did not return Markdown. Existing note was preserved.')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_BYTES):
                raise SyncError('Invalid ledger size. Existing note was preserved.')
            identity = owner_id(response.headers.get('X-Pocket-Owner-ID'))
            if expected_owner is not None and identity != expected_owner:
                raise SyncError('Pocket owner identity changed. Existing note was preserved; check your configuration.')
            payload = response.read(MAX_BYTES + 1)
            if length is not None and len(payload) != int(length):
                raise SyncError('Incomplete ledger download. Existing note was preserved.')
            markdown_text(payload)
            return payload, identity
    except HTTPError as error:
        error.close()
        if error.code in (401, 403):
            raise SyncError('Sync token was rejected or expired. Generate a new token and run setup again.') from None
        raise SyncError('Pocket could not provide the ledger. Existing note was preserved; try again later.') from None
    except (URLError, OSError, TimeoutError):
        raise SyncError('Could not securely reach Pocket. Existing note was preserved; check connectivity and try again.') from None


def source_key(origin, identity):
    return hashlib.sha256((origin_url(origin) + '/' + owner_id(identity)).encode()).hexdigest()


def write_note(vault, payload, dry_run=False, source=None):
    text = markdown_text(payload).rstrip() + '\n'
    vault = Path(vault).expanduser().resolve()
    target = vault / NOTE
    # Permit a user-selected vault root, but no redirects inside the managed path.
    for path in [target, *target.parents]:
        if path == vault:
            break
        if path.is_symlink():
            raise SyncError('The managed note path must not contain symlinks.')
    previous = target.read_bytes() if target.exists() else None
    if previous is not None:
        try:
            old = previous.decode('utf-8')
        except UnicodeDecodeError:
            raise SyncError('Destination contains non-Markdown content. Nothing was overwritten.') from None
        if not old.startswith(GENERATED + '\n'):
            raise SyncError('Destination contains manual notes. Nothing was overwritten.')
        if '<!-- pocket-vps-integrity:' in old:
            match = re.fullmatch(r'(.*\n)<!-- pocket-vps-integrity:([a-f0-9]{64}) -->\n', old, re.DOTALL)
            if not match or hashlib.sha256(match[1].encode()).hexdigest() != match[2]:
                raise SyncError('The synced note has manual edits. Save those separately before syncing again.')
        previous_source = re.search(r'<!-- pocket-vps-source:([a-f0-9]{64}) -->', old)
        if previous_source and previous_source[1] != source:
            raise SyncError('This note belongs to a different Pocket connection. Nothing was overwritten.')
    content = text + '\n' + REMOTE_COPY + '\n'
    if source:
        if not re.fullmatch(r'[a-f0-9]{64}', source):
            raise SyncError('Invalid connection identity.')
        content += '<!-- pocket-vps-source:' + source + ' -->\n'
    content += '<!-- pocket-vps-integrity:' + hashlib.sha256(content.encode()).hexdigest() + ' -->\n'
    encoded = content.encode('utf-8')
    if previous == encoded:
        return 'Already up to date.'
    if dry_run:
        return 'Owner ledger is available; the local note would be updated. No files changed.'
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pocket-sync-', suffix='.tmp', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        if temporary.read_bytes() != encoded:
            raise SyncError('Write verification failed. Existing note was preserved.')
        if target.is_symlink() or (target.read_bytes() if target.exists() else None) != previous:
            raise SyncError('The note changed while syncing. Nothing was overwritten; try again.')
        temporary.replace(target)
        if target.read_bytes() != encoded:
            raise SyncError('Read-back verification failed. Check the note before retrying.')
    finally:
        temporary.unlink(missing_ok=True)
    return 'Owner ledger updated in Obsidian and verified.'


def mac_keychain():
    # Explicit native backend: never accept an environment-selected plaintext backend.
    if sys.platform != 'darwin':
        raise SyncError('The sync helper requires macOS Keychain.')
    try:
        from keyring.backends.macOS import Keyring
        backend = Keyring()
        backend.priority  # Check that the native Security API is available.
        backend.keychain = None
        return backend
    except Exception:
        raise SyncError('macOS Keychain support is unavailable. Install requirements-sync.txt in the helper environment.') from None


def keychain_service(origin):
    return 'in.mrox.pocket.obsidian-sync:' + origin_url(origin)


def read_token(config):
    try:
        token = mac_keychain().get_password(keychain_service(config['origin']), config['ownerId'])
    except SyncError:
        raise
    except Exception:
        raise SyncError('Keychain access failed. Unlock your login Keychain and authorize the sync helper.') from None
    return validate_token(token)


def load_config(path):
    try:
        config = json.loads(Path(path).expanduser().read_text())
        if set(config) != {'version', 'origin', 'ownerId', 'vault'} or config['version'] != 1:
            raise ValueError()
        config['origin'] = origin_url(config['origin'])
        owner_id(config['ownerId'])
        if not isinstance(config['vault'], str) or not Path(config['vault']).is_absolute():
            raise ValueError()
        return config
    except (OSError, ValueError, TypeError):
        raise SyncError('Sync configuration is missing or invalid. Run the setup command.') from None


def save_config(path, config):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise SyncError('The configuration must not be a symlink.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.pocket-config-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(config, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def setup(path, origin, vault):
    origin = origin_url(origin)
    vault = Path(vault).expanduser().resolve()
    if not vault.is_dir():
        raise SyncError('Select an existing Obsidian vault directory.')
    if not sys.stdin.isatty():
        raise SyncError('Run setup in an interactive Terminal so the token can be entered privately.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass('Pocket sync token (hidden): ').strip()
    except (getpass.GetPassWarning, EOFError):
        raise SyncError('Hidden token entry is unavailable. Run setup in an interactive Terminal.') from None
    payload, identity = fetch_owner_note(origin, token)
    config = {'version': 1, 'origin': origin, 'ownerId': identity, 'vault': str(vault)}
    path = Path(path).expanduser()
    if path.exists() and load_config(path) != config:
        raise SyncError('This configuration belongs to another connection or vault. Use a separate config file.')
    # Validate the destination without changing the actual ledger during setup.
    write_note(vault, payload, dry_run=True, source=source_key(origin, identity))
    try:
        backend = mac_keychain()
        backend.set_password(keychain_service(origin), identity, token)
        stored = backend.get_password(keychain_service(origin), identity)
        if not stored or not secrets.compare_digest(stored, token):
            raise SyncError('Keychain storage could not be verified. Setup is incomplete.')
    except SyncError:
        raise
    except Exception:
        raise SyncError('Could not store the token in Keychain. Unlock Keychain and retry setup.') from None
    save_config(path, config)
    return 'Token verified and stored in Keychain. Configuration saved; no ledger or schedule changed.'


def sync_once(path, dry_run=False):
    config = load_config(path)
    # A missing/cloud-unmounted vault should not become a new empty folder.
    if not Path(config['vault']).is_dir():
        raise SyncError('The configured vault is unavailable. Existing note was preserved.')
    payload, identity = fetch_owner_note(config['origin'], read_token(config), config['ownerId'])
    return write_note(config['vault'], payload, dry_run, source_key(config['origin'], identity))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--setup', action='store_true', help='Privately enter a token, verify it, and store it in Keychain.')
    parser.add_argument('--url', help='Pocket HTTPS origin, needed only during setup.')
    parser.add_argument('--vault', type=Path, help='Existing Obsidian vault, needed only during setup.')
    parser.add_argument('--dry-run', action='store_true', help='Verify the connection without changing the note.')
    args = parser.parse_args(argv)
    if args.setup and (not args.url or not args.vault or args.dry_run):
        parser.error('--setup requires --url and --vault, without --dry-run.')
    if not args.setup and (args.url or args.vault):
        parser.error('--url and --vault are only used with --setup.')
    try:
        message = setup(args.config, args.url, args.vault) if args.setup else sync_once(args.config, args.dry_run)
    except SyncError as error:
        message, status = str(error), 1
    except KeyboardInterrupt:
        message, status = 'Sync cancelled.', 1
    except Exception:
        # Exception details may contain server responses, paths, or a credential.
        message, status = 'Sync failed. Check connectivity, configuration, Keychain, and vault permissions; no completion was confirmed.', 1
    else:
        status = 0
    print(datetime.now(timezone.utc).isoformat(timespec='seconds') + ' ' + message, file=sys.stderr if status else sys.stdout)
    return status


if __name__ == '__main__':
    raise SystemExit(main())

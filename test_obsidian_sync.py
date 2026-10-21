"""Safe owner-note import checks, using only temporary files."""
from pathlib import Path
import tempfile
import unittest
from deploy.sync_obsidian import GENERATED, NOTE, REMOTE_COPY, write_note
from server import Ledger


class ObsidianSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vault = self.root/'vault'
        self.payload = (GENERATED + '\n# Expense Ledger\n\nExample owner record\n').encode()

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_verified_and_repeat_is_unchanged(self):
        self.assertIn('verified', write_note(self.vault, self.payload))
        target = self.vault/NOTE
        self.assertIn(REMOTE_COPY, target.read_text())
        timestamp = target.stat().st_mtime_ns
        self.assertEqual(write_note(self.vault, self.payload), 'Already up to date.')
        self.assertEqual(timestamp, target.stat().st_mtime_ns)

    def test_dry_run_does_not_create_vault(self):
        self.assertIn('No files changed', write_note(self.vault, self.payload, dry_run=True))
        self.assertFalse(self.vault.exists())

    def test_manual_notes_and_invalid_download_are_preserved(self):
        target = self.vault/NOTE
        target.parent.mkdir(parents=True)
        target.write_text('My manual note')
        for payload in [self.payload, b'<html>Login required</html>', b'', b'\xff']:
            with self.assertRaises(ValueError): write_note(self.vault, payload)
            self.assertEqual(target.read_text(), 'My manual note')

    def test_local_app_cannot_overwrite_remote_note(self):
        write_note(self.vault, self.payload)
        original = (self.vault/NOTE).read_bytes()
        local = Ledger(self.root/'data', str(self.vault))
        self.assertFalse(local.sync['ok'])
        self.assertIn('synchronized from your VPS', local.sync['message'])
        self.assertEqual((self.vault/NOTE).read_bytes(), original)



class HTTPAndKeychainTests(unittest.TestCase):
    def setUp(self):
        import uuid
        from unittest.mock import patch
        from deploy import sync_obsidian
        self.sync = sync_obsidian
        self.patch = patch
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = (self.root/'vault').resolve(); self.vault.mkdir()
        self.owner = str(uuid.uuid4())
        self.origin = 'https://pocket.example.com'
        self.token = 'pocket_sync_' + 'a' * 43  # Synthetic; never a real credential.
        self.payload = (GENERATED + '\n# Expense Ledger\n\nSynthetic private finance entry\n').encode()
        self.config_path = self.root/'sync.json'
        self.config = {'version':1, 'origin':self.origin, 'ownerId':self.owner, 'vault':str(self.vault)}
        self.sync.save_config(self.config_path, self.config)
        self.source = self.sync.source_key(self.origin, self.owner)
        self.sync.write_note(self.vault, self.payload, source=self.source)
        self.original = (self.vault/NOTE).read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def response(self, payload=None, **headers):
        from email.message import Message
        from io import BytesIO
        result = BytesIO(self.payload if payload is None else payload)
        result.status = 200
        result.geturl = lambda: self.origin + self.sync.ENDPOINT
        result.headers = Message()
        for key, value in {'Content-Type':'text/markdown; charset=utf-8', 'X-Pocket-Owner-ID':self.owner, **headers}.items():
            result.headers[key] = value
        return result

    def test_https_tls_and_fixed_endpoint(self):
        import ssl
        with self.patch.object(self.sync, 'build_opener') as factory:
            factory.return_value.open.return_value = self.response()
            payload, owner = self.sync.fetch_owner_note(self.origin, self.token, self.owner)
            self.assertEqual((payload, owner), (self.payload, self.owner))
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, self.origin + self.sync.ENDPOINT)
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.token)
            self.assertEqual(factory.return_value.open.call_args.kwargs['timeout'], 30)
            context = factory.call_args.args[2]._context
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        for url in ['http://pocket.example.com', 'https://user:secret@pocket.example.com', self.origin+'/?token=secret', self.origin+'/#secret', self.origin+'/other', 'https://pocket.example.com:bad']:
            with self.subTest(url=url), self.patch.object(self.sync, 'build_opener') as factory:
                with self.assertRaises(self.sync.SyncError): self.sync.fetch_owner_note(url, self.token)
                factory.assert_not_called()

    def test_redirect_does_not_forward_credentials(self):
        from email.message import Message
        from urllib.request import Request
        request = Request(self.origin+self.sync.ENDPOINT, headers={'Authorization':'Bearer '+self.token})
        headers = Message(); headers['Location'] = 'https://elsewhere.example/ledger'
        with self.assertRaises(self.sync.SyncError):
            self.sync.NoRedirect().http_error_302(request, None, 302, 'Found', headers)

    def test_bad_responses_preserve_note(self):
        import uuid
        responses = [
            self.response(b'<html>Login required</html>', **{'Content-Type':'text/html'}),
            self.response(b'not a generated ledger'), self.response(b'\xff'), self.response(b''),
            self.response(**{'X-Pocket-Owner-ID':str(uuid.uuid4())}),
            self.response(**{'X-Pocket-Owner-ID':'invalid'}),
            self.response(**{'Content-Length':'1'}),
            self.response(**{'Content-Length':str(self.sync.MAX_BYTES+1)}),
            self.response(**{'Content-Encoding':'gzip'}),
        ]
        for response in responses:
            with self.subTest(headers=str(response.headers)), self.patch.object(self.sync, 'read_token', return_value=self.token), self.patch.object(self.sync, 'build_opener') as factory:
                factory.return_value.open.return_value = response
                with self.assertRaises(self.sync.SyncError): self.sync.sync_once(self.config_path)
                self.assertEqual((self.vault/NOTE).read_bytes(), self.original)

    def test_failures_log_no_credentials_finances_or_exception_details(self):
        import contextlib
        import io
        from urllib.error import HTTPError, URLError
        errors = [URLError(self.token), TimeoutError(self.token), OSError(self.token),
                  HTTPError(self.origin,401,self.token,{},None), HTTPError(self.origin,503,self.token,{},None),
                  RuntimeError(self.token + self.payload.decode())]
        for error in errors:
            with self.subTest(kind=type(error)), self.patch.object(self.sync, 'read_token', return_value=self.token), self.patch.object(self.sync, 'build_opener') as factory:
                factory.return_value.open.side_effect = error
                output = io.StringIO()
                with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
                    self.assertEqual(self.sync.main(['--config', str(self.config_path)]), 1)
                self.assertNotIn(self.token, output.getvalue())
                self.assertNotIn('Synthetic private finance entry', output.getvalue())
                self.assertEqual((self.vault/NOTE).read_bytes(), self.original)

    def test_keychain_failure_or_missing_token_prevents_network_access(self):
        for value in [None, 'wrong', RuntimeError(self.token)]:
            with self.patch.object(self.sync, 'mac_keychain') as keychain, self.patch.object(self.sync, 'fetch_owner_note') as fetch:
                if isinstance(value, Exception): keychain.return_value.get_password.side_effect = value
                else: keychain.return_value.get_password.return_value = value
                with self.assertRaises(self.sync.SyncError): self.sync.sync_once(self.config_path)
                fetch.assert_not_called()
                self.assertEqual((self.vault/NOTE).read_bytes(), self.original)

    def test_setup_only_stores_secret_in_keychain_and_keeps_note(self):
        with self.patch.object(self.sync.sys.stdin, 'isatty', return_value=True), self.patch.object(self.sync.getpass, 'getpass', return_value=self.token), self.patch.object(self.sync, 'fetch_owner_note', return_value=(self.payload,self.owner)), self.patch.object(self.sync, 'mac_keychain') as keychain:
            keychain.return_value.get_password.return_value = self.token
            self.assertIn('Keychain', self.sync.setup(self.config_path, self.origin, self.vault))
            keychain.return_value.set_password.assert_called_once_with(self.sync.keychain_service(self.origin), self.owner, self.token)
        self.assertNotIn(self.token, self.config_path.read_text())
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.vault/NOTE).read_bytes(), self.original)
        with self.patch.object(self.sync.sys.stdin, 'isatty', return_value=False), self.patch.object(self.sync.getpass, 'getpass') as prompt:
            with self.assertRaises(self.sync.SyncError): self.sync.setup(self.config_path, self.origin, self.vault)
            prompt.assert_not_called()

    def test_setup_validates_before_touching_keychain(self):
        with self.patch.object(self.sync.sys.stdin, 'isatty', return_value=True), self.patch.object(self.sync.getpass, 'getpass', return_value=self.token), self.patch.object(self.sync, 'fetch_owner_note', side_effect=self.sync.SyncError('Invalid token.')), self.patch.object(self.sync, 'mac_keychain') as keychain:
            with self.assertRaises(self.sync.SyncError): self.sync.setup(self.config_path, self.origin, self.vault)
            keychain.assert_not_called()
        (self.vault/NOTE).write_text('My manual note')
        with self.patch.object(self.sync.sys.stdin, 'isatty', return_value=True), self.patch.object(self.sync.getpass, 'getpass', return_value=self.token), self.patch.object(self.sync, 'fetch_owner_note', return_value=(self.payload,self.owner)), self.patch.object(self.sync, 'mac_keychain') as keychain:
            with self.assertRaises(self.sync.SyncError): self.sync.setup(self.config_path, self.origin, self.vault)
            keychain.assert_not_called()

    def test_manual_edits_and_connection_changes_are_preserved(self):
        (self.vault/NOTE).write_bytes(self.original+b'My manual addition\n')
        with self.assertRaises(self.sync.SyncError): self.sync.write_note(self.vault, self.payload, source=self.source)
        self.assertIn(b'My manual addition', (self.vault/NOTE).read_bytes())
        (self.vault/NOTE).write_bytes(self.original)
        with self.assertRaises(self.sync.SyncError): self.sync.write_note(self.vault, self.payload, source='b'*64)
        self.assertEqual((self.vault/NOTE).read_bytes(), self.original)

    def test_symlink_path_cannot_redirect_writes(self):
        alternate = self.root/'alternate'; alternate.mkdir()
        alternate.joinpath('Expense Ledger.md').write_text('Manual note')
        (self.vault/NOTE).unlink()
        (self.vault/NOTE).parent.rmdir()
        (self.vault/NOTE).parent.symlink_to(alternate, target_is_directory=True)
        with self.assertRaises(self.sync.SyncError): self.sync.write_note(self.vault, self.payload)
        self.assertEqual(alternate.joinpath('Expense Ledger.md').read_text(), 'Manual note')

    def test_failed_atomic_write_preserves_prior_note(self):
        with self.patch.object(self.sync.os, 'fsync', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError): self.sync.write_note(self.vault, self.payload+b'New entry\n', source=self.source)
        self.assertEqual((self.vault/NOTE).read_bytes(), self.original)
        self.assertFalse(list((self.vault/NOTE).parent.glob('.pocket-sync-*')))

    def test_helper_uses_native_backend_without_plaintext_fallback(self):
        with self.patch.object(self.sync.sys, 'platform', 'linux'):
            with self.assertRaises(self.sync.SyncError): self.sync.mac_keychain()

    def test_success_and_dry_run_logs_contain_no_records(self):
        import contextlib
        import io
        with self.patch.object(self.sync, 'read_token', return_value=self.token), self.patch.object(self.sync, 'build_opener') as factory:
            factory.return_value.open.return_value = self.response(self.payload+b'Updated secret amount\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.sync.main(['--config',str(self.config_path),'--dry-run']), 0)
        self.assertNotIn('Updated secret amount', output.getvalue())
        self.assertNotIn(self.token, output.getvalue())
        self.assertEqual((self.vault/NOTE).read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()

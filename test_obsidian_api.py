"""Owner-only token boundary, persistence, backup and complete sync checks."""
import json
import sqlite3
import time
import unittest
from unittest.mock import patch
from email.message import Message
from io import BytesIO

from accounts import digest, SYNC_SCOPE, SYNC_SECONDS
from deploy.backup import make_backup
from deploy.restore import restore
from deploy import sync_obsidian as sync
from production import create_app
from test_production import ProductionFixture


class ObsidianAPITests(ProductionFixture, unittest.TestCase):
    def issue(self):
        response = self.post(self.client, '/api/obsidian/token', {})
        self.assertEqual(response.status_code, 200)
        return response.json

    def download(self, token=None, client=None, **kwargs):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        return (client or self.app.test_client()).get('/api/obsidian/ledger.md', base_url=self.base, headers=headers, **kwargs)

    def test_hash_only_once_expiry_and_restart(self):
        issued = self.issue()
        token = issued['token']
        self.assertEqual(issued['expiresAt'] - issued['createdAt'], SYNC_SECONDS)
        self.assertEqual(issued['scope'], SYNC_SCOPE)
        with self.accounts.connect() as conn:
            row = conn.execute('SELECT * FROM sync_tokens').fetchone()
            self.assertEqual(row['token_hash'], digest(token))
            self.assertNotIn(token, json.dumps(dict(row)))
        for path in ['/api/obsidian/token', '/api/state', '/api/export.json']:
            self.assertNotIn(token, self.get(self.client, path).text)
        restart = create_app(self.settings)
        self.assertEqual(self.download(token, restart.test_client()).status_code, 200)
        with patch('accounts.time.time', return_value=issued['expiresAt']):
            self.assertEqual(self.download(token).status_code, 401)
            self.assertFalse(self.get(self.client, '/api/obsidian/token').json.get('active', False))
        self.assertNotIn(token.encode(), self.accounts.db.read_bytes())

    def test_rotation_and_revocation(self):
        first = self.issue()['token']
        second = self.issue()['token']
        self.assertNotEqual(first, second)
        self.assertEqual(self.download(first).status_code, 401)
        self.assertEqual(self.download(second).status_code, 200)
        response = self.post(self.client, '/api/obsidian/token/revoke', {})
        self.assertFalse(response.json['active'])
        self.assertEqual(self.download(second).status_code, 401)
        self.assertIsNone(create_app(self.settings).extensions['pocket_accounts'].sync_owner(second))

    def test_cookie_and_basic_auth_cannot_replace_sync_token(self):
        token = self.issue()['token']
        for client in [self.app.test_client(), self.client, self.signup()]:
            response = self.download(client=client)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('WWW-Authenticate', response.headers)
        for credential in ['Basic abc', 'Bearer wrong', 'Bearer ' + token + 'x']:
            response = self.client.get('/api/obsidian/ledger.md', base_url=self.base, headers={'Authorization': credential})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get('/api/obsidian/ledger.md', base_url='http://pocket.example.com', headers={'Authorization': 'Bearer ' + token}).status_code, 403)

    def test_only_owner_can_manage_tokens_even_with_forged_account(self):
        other = self.signup()
        owner = self.get(self.client, '/api/state').json['account']['id']
        self.assertEqual(self.get(other, '/api/obsidian/token').status_code, 403)
        for route in ['/api/obsidian/token', '/api/obsidian/token/revoke']:
            self.assertEqual(self.post(other, route, {'user_id': owner, 'is_owner': True}).status_code, 403)
        with self.accounts.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM sync_tokens').fetchone()[0], 0)

    def test_token_management_requires_origin_and_csrf(self):
        token = self.issue()['token']
        for route in ['/api/obsidian/token', '/api/obsidian/token/revoke']:
            for headers in [{}, {'Origin':self.base,'X-LifeOS-Token':'wrong'}, {'Origin':'https://evil.example','X-LifeOS-Token':self.token(self.client)}]:
                self.assertEqual(self.client.post(route, base_url=self.base, headers=headers, json={}).status_code, 403)
        self.assertEqual(self.download(token).status_code, 200)

    def test_bearer_has_no_other_read_or_write_access(self):
        token = self.issue()['token']
        client = self.app.test_client()
        headers = {'Authorization':'Bearer ' + token, 'Origin':self.base}
        for route in ['/api/state','/api/export.json','/api/ledger.md','/api/reminder.ics','/api/obsidian/token']:
            self.assertEqual(client.get(route, base_url=self.base, headers=headers).status_code, 401)
        for route in ['/api/entries','/api/repayments','/api/reminders','/logout','/api/obsidian/token','/api/obsidian/token/revoke']:
            self.assertEqual(client.post(route, base_url=self.base, headers=headers, json={}).status_code, 401)
        self.assertEqual(client.post('/api/obsidian/ledger.md', base_url=self.base, headers=headers).status_code, 405)
        with self.accounts.connect() as conn:
            conn.execute('UPDATE sync_tokens SET scope=?', ('other:read',))
        self.assertEqual(self.download(token).status_code, 401)

    def test_owner_export_contains_only_owner_records_and_no_account_secrets(self):
        self.save(title='Only owner expense')
        other = self.signup()
        self.save(other, title='Secret other expense')
        issued = self.issue()
        response = self.download(issued['token'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/markdown')
        self.assertEqual(response.headers['X-Pocket-Owner-ID'], issued['ownerId'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertIn('Only owner expense', response.text)
        for secret in ['Secret other expense', self.password_hash, issued['token'], 'second-user']:
            self.assertNotIn(secret, response.text)
        self.assertEqual(self.download(issued['token'], query_string={'accountId':self.get(other,'/api/state').json['account']['id']}).status_code, 400)
        # Even a mistakenly inserted non-owner token is rejected at verification.
        other_id = self.get(other, '/api/state').json['account']['id']
        with self.accounts.connect() as conn:
            conn.execute('UPDATE sync_tokens SET user_id=?', (other_id,))
        self.assertEqual(self.download(issued['token']).status_code, 401)

    def test_password_reset_and_restore_revoke_tokens(self):
        issued = self.issue()
        self.signup()
        self.accounts.reset_password('second-user', 'another-synthetic-password')
        self.assertEqual(self.download(issued['token']).status_code, 200)
        self.save(title='Owner record to restore')
        snapshot = make_backup(self.root/'data', self.root/'snapshots')
        restored_dir = self.root/'restored'
        restore(snapshot, restored_dir)
        settings = {**self.settings, 'POCKET_DATA_DIR':str(restored_dir), 'POCKET_VAULT':str(self.root/'restored-vault')}
        restored_app = create_app(settings)
        self.assertEqual(self.download(issued['token'], restored_app.test_client()).status_code, 401)
        with sqlite3.connect(restored_dir/'ledger.sqlite3') as conn:
            self.assertIn('Owner record to restore', conn.execute('SELECT payload FROM entries').fetchone()[0])
        self.accounts.reset_password('tester', 'another-synthetic-password')
        self.assertEqual(self.download(issued['token']).status_code, 401)

    def test_unavailable_export_is_not_downloaded_as_a_ledger(self):
        issued = self.issue()
        disabled = create_app({**self.settings, 'POCKET_VAULT':''})
        response = self.download(issued['token'], disabled.test_client())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.mimetype, 'application/json')

    def test_end_to_end_api_to_temporary_vault_using_keychain_adapter(self):
        self.save(title='Scooter repair', amount='14000')
        other = self.signup()
        self.save(other, title='Never copied')
        issued = self.issue()
        vault = self.root/'mac-vault'; vault.mkdir()
        config = {'version':1, 'origin':self.base, 'ownerId':issued['ownerId'], 'vault':str(vault)}
        path = self.root/'sync.json'
        sync.save_config(path, config)
        test = self
        class FlaskOpener:
            def open(self, request, timeout):
                response = test.app.test_client().get(sync.ENDPOINT, base_url=test.base, headers=dict(request.header_items()))
                result = BytesIO(response.data)
                result.status = response.status_code
                result.headers = Message()
                for key, value in response.headers.items(): result.headers[key] = value
                result.geturl = lambda: request.full_url
                return result
        with patch.object(sync, 'mac_keychain') as keychain, patch.object(sync, 'build_opener', return_value=FlaskOpener()):
            keychain.return_value.get_password.return_value = issued['token']
            self.assertIn('verified', sync.sync_once(path))
        note = (vault/sync.NOTE).read_text()
        self.assertIn('Scooter repair', note)
        self.assertNotIn('Never copied', note)
        self.assertNotIn(issued['token'], note + path.read_text())
        self.assertIn(sync.REMOTE_COPY, note)


if __name__ == '__main__':
    unittest.main()

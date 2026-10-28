"""Compact page authentication and owner sync, using disposable accounts only."""
from email.message import Message
from io import BytesIO
import unittest
from unittest.mock import patch

from deploy import sync_obsidian as sync
from test_production import ProductionFixture


class CompanionTests(ProductionFixture, unittest.TestCase):
    def test_signed_out_compact_page_and_login_return(self):
        client = self.app.test_client()
        response = self.get(client, '/quick-entry')
        self.assertEqual((response.status_code, response.location), (303, '/login?next=/quick-entry'))
        self.assertNotIn('WWW-Authenticate', response.headers)
        page = self.get(client, response.location)
        self.assertIn('data-pocket-view="quick-entry"', page.text)
        self.assertIn('action="/login?next=/quick-entry"', page.text)
        self.assertIn('href="/signup?next=/quick-entry"', page.text)
        logged_in = self.post(client, '/login?next=/quick-entry',
                              dict(username='tester', password=self.password), self.token(client, '/login?next=/quick-entry'))
        self.assertEqual(logged_in.json['redirect'], '/quick-entry')
        self.assertEqual(self.get(client, '/login?next=/quick-entry').location, '/quick-entry')

    def test_signup_keeps_compact_destination_and_starts_empty(self):
        client = self.app.test_client()
        token = self.token(client, '/signup?next=/quick-entry')
        response = self.post(client, '/signup?next=/quick-entry',
                             dict(username='popup-user', password=self.password, confirmation=self.password), token)
        self.assertEqual(response.json['redirect'], '/quick-entry')
        self.assertEqual(self.get(client, '/api/state').json['entries'], [])

    def test_return_target_is_allowlisted(self):
        for destination in ['https://evil.example', '//evil.example', '/api/export.json', '/quick-entry?bad=1']:
            client = self.app.test_client()
            path = '/login?next=' + destination
            page = self.get(client, path)
            self.assertNotIn('data-pocket-view=', page.text)
            self.assertNotIn(destination, page.text)
            result = self.post(client, path, dict(username='tester', password=self.password), self.token(client, path))
            self.assertEqual(result.json['redirect'], '/')

    def test_shared_form_and_no_store(self):
        page = self.get(self.client, '/quick-entry')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        self.assertIn('data-pocket-view="quick-entry"', page.text)
        for name in ['app.js', 'companion.js', 'quick_parse.js']:
            self.assertIn('src="/' + name + '"', page.text)
            self.assertEqual(self.get(self.client, '/' + name).headers['Cache-Control'], 'no-store')
        for kind in ['expense', 'loan', 'reminder', 'mandate']:
            self.assertIn('data-kind="' + kind + '"', page.text)
        self.assertNotIn('data-pocket-view="quick-entry"', self.get(self.client, '/').text)

    def test_compact_csrf_logout_and_account_isolation(self):
        other = self.signup('another-popup')
        token = self.token(self.client, '/quick-entry')
        data = dict(kind='expense', title='Private popup expense', amount='200', category='Transport', date='2026-10-27')
        self.assertEqual(self.post(other, '/api/entries', data, token).status_code, 403)
        self.assertEqual(self.post(self.client, '/api/entries', data, token).status_code, 200)
        self.assertEqual(self.get(other, '/api/state').json['entries'], [])
        self.assertEqual(self.post(self.client, '/logout', {}, token).status_code, 200)
        self.assertEqual(self.get(self.client, '/quick-entry').status_code, 303)
        self.assertEqual(self.get(self.client, '/api/state').status_code, 401)
        self.assertEqual(self.post(self.client, '/api/entries', data, token).status_code, 401)

    def test_popup_entry_reaches_isolated_owner_vault(self):
        csrf = self.token(self.client, '/quick-entry')
        response = self.post(self.client, '/api/entries', dict(kind='expense', title='Popup petrol',
            amount='200', category='Transport', date='2026-10-27'), csrf)
        self.assertEqual(response.status_code, 200)
        other = self.signup('popup-neighbour')
        self.save(other, title='Other popup record never exported')
        issued = self.post(self.client, '/api/obsidian/token', {}).json
        vault = self.root / 'isolated-mac-vault'; vault.mkdir()
        config = self.root / 'sync.json'
        sync.save_config(config, dict(version=1, origin=self.base, ownerId=issued['ownerId'], vault=str(vault)))
        test = self

        class Opener:
            def open(self, request, timeout):
                response = test.app.test_client().get(sync.ENDPOINT, base_url=test.base, headers=dict(request.header_items()))
                stream = BytesIO(response.data); stream.status = response.status_code
                stream.headers = Message()
                for key, value in response.headers.items(): stream.headers[key] = value
                stream.geturl = lambda: request.full_url
                return stream

        with patch.object(sync, 'read_token', return_value=issued['token']), patch.object(sync, 'build_opener', return_value=Opener()):
            self.assertIn('verified', sync.sync_once(config))
        note = (vault / sync.NOTE).read_text()
        self.assertIn('Popup petrol', note)
        self.assertNotIn('Other popup record', note)
        self.assertNotIn(issued['token'], note)


if __name__ == '__main__':
    unittest.main()

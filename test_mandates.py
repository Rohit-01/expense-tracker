"""Monthly mandate accounting and account-private category checks."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from unittest.mock import patch

from deploy.backup import make_backup
from deploy.restore import restore
from server import Ledger, mandate_due
from test_production import ProductionFixture


class MandateTests(ProductionFixture, TestCase):
    def add_category(self, client=None):
        client = client or self.client
        self.assertEqual(self.post(client, '/api/categories', {'name': 'Diet Coke', 'parent': 'Food & groceries'}).status_code, 200)

    def add_mandate(self, client=None, **changes):
        data = {'title': 'Netflix', 'amount': '499', 'firstDueDate': '2026-01-31',
                'category': 'Food & groceries', 'subcategory': 'Diet Coke'}
        data.update(changes)
        response = self.post(client or self.client, '/api/mandates', data)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['mandates'][0]

    def test_categories_are_private_and_entries_keep_subcategories(self):
        self.add_category()
        owner = self.save(category='Food & groceries', subcategory='Diet Coke', title='Diet Coke').json
        self.assertEqual(owner['entries'][0]['subcategory'], 'Diet Coke')
        self.assertIn('Diet Coke', self.get(self.client, '/api/ledger.md').text)
        other = self.signup()
        self.assertNotIn('Diet Coke', json.dumps(self.get(other, '/api/state').json['categories']))
        self.assertEqual(self.save(other, category='Food & groceries', subcategory='Diet Coke').status_code, 400)
        self.assertEqual(self.post(other, '/api/categories', {'name':'diet coke', 'parent':'Food & groceries'}).status_code, 200)
        self.assertEqual(self.post(self.client, '/api/categories', {'name':'diet coke', 'parent':'Food & groceries'}).status_code, 400)

    def test_new_mutations_require_csrf(self):
        for path in ['/api/categories', '/api/mandates', '/api/mandate-status', '/api/mandate-payments']:
            self.assertEqual(self.client.post(path, base_url=self.base, json={}).status_code, 403)
        self.assertEqual(mandate_due('2024-02', 31), '2024-02-29')

    @patch('server.today', return_value=dt.date(2026, 3, 3))
    def test_due_day_clamps_overdue_and_paid_expense_is_idempotent(self, current):
        self.add_category()
        mandate = self.add_mandate()
        occurrences = {item['period']: item for item in mandate['occurrences']}
        self.assertEqual(occurrences['2026-02']['dueDate'], '2026-02-28')
        self.assertEqual(occurrences['2026-03']['dueDate'], '2026-03-31')
        self.assertEqual(set(occurrences), {'2026-01', '2026-02', '2026-03'})
        before = len(self.get(self.client, '/api/state').json['entries'])
        payload = {'id': mandate['id'], 'period': '2026-02'}
        first = self.post(self.client, '/api/mandate-payments', payload)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(self.post(self.client, '/api/mandate-payments', payload).status_code, 200)
        state = self.get(self.client, '/api/state').json
        self.assertEqual(len(state['entries']), before + 1)
        expense = state['entries'][0]
        self.assertEqual((expense['date'], expense['mandateId'], expense['mandateMonth']), ('2026-03-03', mandate['id'], '2026-02'))
        self.assertEqual((expense['category'], expense['subcategory']), ('Food & groceries', 'Diet Coke'))
        self.assertEqual(next(x for x in state['mandates'][0]['occurrences'] if x['period']=='2026-02')['status'], 'paid')
        self.assertEqual(next(x for x in state['mandates'][0]['occurrences'] if x['period']=='2026-03')['status'], 'unpaid')
        self.assertIn('2026-02', self.get(self.client, '/api/ledger.md').text)
        self.assertEqual(self.post(self.client, '/api/mandate-payments', {'id':mandate['id'], 'period':'2026-04'}).status_code, 400)
        with patch('server.today', return_value=dt.date(2026, 4, 1)):
            renewed = self.get(self.client, '/api/state').json['mandates'][0]['occurrences']
        self.assertEqual(renewed[0]['period'], '2026-04')
        self.assertEqual(renewed[0]['status'], 'unpaid')
        self.assertEqual(next(x for x in renewed if x['period']=='2026-02')['status'], 'paid')

    @patch('server.today', return_value=dt.date(2026, 2, 15))
    def test_edit_and_pause_keep_paid_history(self, current):
        self.add_category()
        mandate = self.add_mandate(firstDueDate='2026-01-31')
        self.post(self.client, '/api/mandate-payments', {'id': mandate['id'], 'period': '2026-01'})
        edited = self.post(self.client, '/api/mandates', {'id': mandate['id'], 'title': 'New name', 'amount': '599',
            'firstDueDate': '2026-01-31', 'category': 'Other', 'subcategory': ''})
        self.assertEqual(edited.status_code, 200)
        paid = next(x for x in edited.json['mandates'][0]['occurrences'] if x['period']=='2026-01')
        self.assertEqual((paid['paidTitle'],paid['paidAmountMinor']), ('Netflix',49900))
        paused = self.post(self.client, '/api/mandate-status', {'id':mandate['id'], 'paused': True})
        self.assertEqual(paused.status_code, 200)
        self.assertEqual([x['period'] for x in paused.json['mandates'][0]['occurrences']], ['2026-01'])
        resumed = self.post(self.client, '/api/mandate-status', {'id':mandate['id'], 'paused': False})
        self.assertEqual([x['period'] for x in resumed.json['mandates'][0]['occurrences']], ['2026-02','2026-01'])

    @patch('server.today', return_value=dt.date(2026, 10, 22))
    def test_simultaneous_paid_requests_make_one_expense(self, current):
        ledger = Ledger(self.root/'concurrent', '')
        mandate = ledger.save_mandate({'title':'Monthly plan', 'amount':'100',
                                      'firstDueDate':'2026-10-20', 'category':'Other'})['mandates'][0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(ledger.pay_mandate, [{'id':mandate['id'],'period':'2026-10'}] * 2))
        self.assertEqual(len(results), 2)
        self.assertEqual(len(ledger.state()['entries']), 1)
        with sqlite3.connect(ledger.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM mandate_payments').fetchone()[0], 1)

    @patch('server.today', return_value=dt.date(2026, 10, 22))
    def test_private_mandates_backups_and_owner_export(self, current):
        self.add_category()
        mandate = self.add_mandate(firstDueDate='2026-10-20')
        self.post(self.client, '/api/mandate-payments', {'id':mandate['id'], 'period':'2026-10'})
        owner_note = self.root/'vault/ai zone/openai agent/Personal/Finance/Expense Ledger.md'
        owner_content = owner_note.read_text()
        other = self.signup()
        self.assertEqual(self.post(other, '/api/mandate-payments', {'id':mandate['id'], 'period':'2026-10'}).status_code, 400)
        self.assertEqual(self.post(other, '/api/mandate-status', {'id':mandate['id'], 'paused':True}).status_code, 400)
        self.assertEqual(self.post(other, '/api/mandates', {'id':mandate['id'], 'title':'Intrusion', 'amount':'1', 'firstDueDate':'2026-10-20','category':'Other'}).status_code, 400)
        other_mandate = self.add_mandate(other, title='Other private mandate', category='Other', subcategory='')
        self.post(other, '/api/mandate-payments', {'id':other_mandate['id'], 'period':'2026-10'})
        self.assertEqual(owner_note.read_text(), owner_content)
        self.assertNotIn('Other private mandate', self.get(self.client, '/api/export.json').text)
        self.assertNotIn('Netflix', self.get(other, '/api/export.json').text)
        backup = make_backup(self.root/'data', self.root/'backups')
        restored = self.root/'restored'
        restore(backup, restored)
        owner = Ledger(restored, '')
        owner_state = owner.state()
        self.assertEqual(owner_state['mandates'][0]['occurrences'][0]['status'], 'paid')
        self.assertEqual(owner_state['entries'][0]['mandateMonth'], '2026-10')
        self.assertIn('Diet Coke', json.dumps(owner_state['categories']))
        with sqlite3.connect(owner.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM mandate_payments').fetchone()[0], 1)

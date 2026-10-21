"""Run with: .venv/bin/python -m unittest test_production -v"""
import base64
import json
from pathlib import Path
import re
import tempfile
import unittest
from werkzeug.security import generate_password_hash
from production import create_app


class ProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 'isolated-test-password-not-a-real-login'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        auth = root/'auth.json'
        auth.write_text(json.dumps({'username':'tester','passwordHash':self.password_hash}))
        self.settings = {'POCKET_DOMAIN':'pocket.example.com','POCKET_AUTH_FILE':str(auth),'POCKET_DATA_DIR':str(root/'data'),'POCKET_VAULT':str(root/'vault')}
        self.app = create_app(self.settings)
        self.client = self.app.test_client()
        basic = base64.b64encode(('tester:'+self.password).encode()).decode()
        self.headers = {'Authorization':'Basic '+basic}
        self.base = 'https://pocket.example.com'
        page = self.client.get('/',base_url=self.base,headers=self.headers)
        self.token = re.search(r'name="lifeos-token" content="([^"]+)"',page.text)[1]

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **fields):
        data=dict(kind='expense',title='Test expense',amount='25.10',person='',category='Other',date='2026-10-21',dueAt='',notes='')
        data.update(fields)
        headers={**self.headers,'Origin':self.base,'X-LifeOS-Token':self.token}
        return self.client.post('/api/entries',base_url=self.base,headers=headers,json=data)

    def test_all_data_requires_login(self):
        for path in ['/', '/app.js','/style.css','/api/state','/api/export.json','/api/ledger.md','/api/reminder.ics']:
            response=self.client.get(path,base_url=self.base)
            self.assertEqual(response.status_code,401,path)
        self.assertEqual(self.client.get('/healthz',base_url=self.base).json,{'status':'ok'})

    def test_https_and_host_checks(self):
        self.assertEqual(self.client.get('/api/state',base_url='http://pocket.example.com',headers=self.headers).status_code,403)
        self.assertEqual(self.client.get('/api/state',base_url='https://other.example.com',headers=self.headers).status_code,400)
        forwarded={**self.headers,'X-Forwarded-Proto':'https'}
        self.assertEqual(self.client.get('/api/state',base_url='http://pocket.example.com',headers=forwarded).status_code,200)

    def test_csrf_and_origin_required(self):
        self.assertEqual(self.client.post('/api/entries',base_url=self.base,headers=self.headers,json={}).status_code,403)
        bad={**self.headers,'Origin':'https://attacker.example.com','X-LifeOS-Token':self.token}
        self.assertEqual(self.client.post('/api/entries',base_url=self.base,headers=bad,json={}).status_code,403)
        missing={**self.headers,'Origin':self.base}
        self.assertEqual(self.client.post('/api/entries',base_url=self.base,headers=missing,json={}).status_code,403)

    def test_save_and_persistent_export(self):
        result=self.save()
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['entries'][0]['amountMinor'],2510)
        self.assertEqual(result.json['storageLocation'],'vps')
        self.assertTrue(result.json['sync']['remoteExport'])
        note=self.client.get('/api/ledger.md',base_url=self.base,headers=self.headers)
        self.assertEqual(note.status_code,200)
        self.assertIn('Test expense',note.text)
        new_client=create_app(self.settings).test_client()
        self.assertEqual(len(new_client.get('/api/state',base_url=self.base,headers=self.headers).json['entries']),1)

    def test_repayment_and_no_overpayment(self):
        loan=self.save(kind='loan',person='Example person',amount='100',dueAt='2026-10-22T09:00').json['entries'][0]
        headers={**self.headers,'Origin':self.base,'X-LifeOS-Token':self.token}
        data=dict(entryId=loan['id'],amount='40',date='2026-10-21',notes='')
        response=self.client.post('/api/repayments',base_url=self.base,headers=headers,json=data)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['repayments'][0]['amountMinor'],4000)
        data['amount']='61'
        self.assertEqual(self.client.post('/api/repayments',base_url=self.base,headers=headers,json=data).status_code,400)
        calendar=self.client.get('/api/reminder.ics?id='+loan['id'],base_url=self.base,headers=self.headers)
        self.assertEqual(calendar.status_code,200)
        self.assertIn('DTSTART:20261022T033000Z',calendar.text)

    def test_login_rate_limit(self):
        incorrect={'Authorization':'Basic '+base64.b64encode(b'tester:wrong').decode()}
        for i in range(10):
            self.assertEqual(self.client.get('/api/state',base_url=self.base,headers=incorrect).status_code,401)
        self.assertEqual(self.client.get('/api/state',base_url=self.base,headers=incorrect).status_code,429)

    def test_never_serves_files_or_credentials(self):
        for path in ['/auth.json','/data/ledger.sqlite3','/config.json','/production.py']:
            self.assertEqual(self.client.get(path,base_url=self.base,headers=self.headers).status_code,404)

    def test_missing_credentials_fail_closed(self):
        settings={**self.settings,'POCKET_AUTH_FILE':str(Path(self.temp.name)/'missing.json')}
        with self.assertRaises(RuntimeError):create_app(settings)


if __name__ == '__main__':
    unittest.main()

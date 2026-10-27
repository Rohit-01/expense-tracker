"""Isolated account, privacy, migration, and backup/restore checks."""
import json
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from werkzeug.security import generate_password_hash
from accounts import COOKIE, digest
from deploy.backup import make_backup
from deploy.restore import restore
from production import create_app
from server import Ledger


class ProductionFixture:
    @classmethod
    def setUpClass(cls):
        cls.password = 'isolated-test-password-not-a-real-login'
        cls.password_hash = generate_password_hash(cls.password, method='scrypt')
        cls.base = 'https://pocket.example.com'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.auth = self.root/'auth.json'
        self.auth.write_text(json.dumps({'username':'tester','passwordHash':self.password_hash}))
        self.settings = {'POCKET_DOMAIN':'pocket.example.com','POCKET_AUTH_FILE':str(self.auth),'POCKET_DATA_DIR':str(self.root/'data'),'POCKET_VAULT':str(self.root/'vault')}
        self.app = create_app(self.settings)
        self.accounts = self.app.extensions['pocket_accounts']
        self.client = self.app.test_client()
        self.assertEqual(self.login(self.client).status_code, 200)

    def tearDown(self):
        self.temp.cleanup()

    def get(self, client, path):
        return client.get(path, base_url=self.base)

    def token(self, client, path='/'):
        return re.search(r'name="lifeos-token" content="([^"]+)"', self.get(client,path).text)[1]

    def post(self, client, path, data, token=None):
        return client.post(path,base_url=self.base,headers={'Origin':self.base,'X-LifeOS-Token':token or self.token(client)},json=data)

    def login(self, client, username='tester', password=None):
        return self.post(client,'/login',dict(username=username,password=password or self.password),self.token(client,'/login'))

    def signup(self, name='second-user'):
        client = self.app.test_client()
        response = self.post(client,'/signup',dict(username=name,password=self.password,confirmation=self.password),self.token(client,'/signup'))
        self.assertEqual(response.status_code,200,response.json)
        return client

    def save(self, client=None, **fields):
        data=dict(kind='expense',title='Owner expense',amount='25.10',person='',category='Other',date='2026-10-21',dueAt='',notes='')
        data.update(fields)
        return self.post(client or self.client,'/api/entries',data)


class ProductionTests(ProductionFixture, unittest.TestCase):
    def test_signed_out_pages_and_no_browser_challenge(self):
        client=self.app.test_client()
        response=self.get(client,'/')
        self.assertEqual((response.status_code,response.location),(303,'/login'))
        for path in ['/login','/signup','/auth.js','/install.js','/style.css','/healthz']:
            self.assertEqual(self.get(client,path).status_code,200,path)
        for path in ['/api/state','/api/export.json','/api/ledger.md','/api/reminder.ics','/app.js','/quick_parse.js','/journal.js']:
            response=self.get(client,path)
            self.assertEqual(response.status_code,401,path)
            self.assertNotIn('WWW-Authenticate',response.headers)
        response=client.get('/api/state',base_url=self.base,headers={'Authorization':'Basic dGVzdGVyOndyb25n'})
        self.assertEqual(response.status_code,401)
        self.assertNotIn('WWW-Authenticate',response.headers)

    def test_home_screen_manifest_and_icons_are_public_without_exposing_records(self):
        client = self.app.test_client()
        for page in ['/login', '/signup', '/']:
            response = self.get(client if page != '/' else self.client, page)
            self.assertIn('rel="manifest" href="/manifest.webmanifest"', response.text)
            self.assertIn('rel="apple-touch-icon" href="/icons/apple-touch-icon.png"', response.text)
            self.assertIn('rel="icon" href="/favicon.ico" sizes="any"', response.text)
            self.assertIn('href="/icons/favicon-32.png"', response.text)
            self.assertIn('<script src="/install.js" defer></script>', response.text)
            self.assertIn('data-install-action', response.text)
            if page == '/':
                self.assertIn('<script src="/quick_parse.js" defer></script>', response.text)
                self.assertIn('<script src="/journal.js" defer></script>', response.text)
                self.assertIn('id="journal-month"', response.text)
                self.assertIn('id="calculate-total"', response.text)
                self.assertIn('id="settle-dialog"', response.text)
        response = self.get(client, '/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/manifest+json')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json['start_url'], '/')
        self.assertEqual(response.json['display'], 'standalone')
        self.assertEqual(response.json['scope'], '/')
        for icon in response.json['icons']:
            image = self.get(client, icon['src'])
            self.assertEqual((image.status_code, image.mimetype), (200, 'image/png'))
            self.assertEqual(image.data[:8], b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', image.data[16:24])
            self.assertEqual((width, height), tuple(map(int, icon['sizes'].split('x'))))
        self.assertEqual(self.get(client, '/icons/apple-touch-icon.png').status_code, 200)
        install_script = self.get(client, '/install.js')
        self.assertEqual(install_script.mimetype, 'text/javascript')
        self.assertEqual(install_script.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.get(self.client, '/quick_parse.js').mimetype, 'text/javascript')
        self.assertEqual(self.get(self.client, '/journal.js').mimetype, 'text/javascript')
        favicon = self.get(client, '/favicon.ico')
        self.assertEqual((favicon.status_code, favicon.mimetype), (200, 'image/x-icon'))
        self.assertEqual(favicon.data[:4], b'\x00\x00\x01\x00')
        small_icon = self.get(client, '/icons/favicon-32.png')
        self.assertEqual((small_icon.status_code, small_icon.mimetype), (200, 'image/png'))
        self.assertEqual(struct.unpack('>II', small_icon.data[16:24]), (32, 32))
        self.assertEqual(self.get(client, '/api/state').status_code, 401)

    def test_https_host_proxy_and_headers(self):
        self.assertEqual(self.client.get('/api/state',base_url='http://pocket.example.com').status_code,403)
        self.assertEqual(self.client.get('/api/state',base_url='https://other.example.com').status_code,400)
        response=self.client.get('/api/state',base_url='http://pocket.example.com',headers={'X-Forwarded-Proto':'https'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])

    def test_csrf_for_auth_and_mutations(self):
        client=self.app.test_client()
        anon=self.token(client,'/login')
        for path in ['/login','/signup']:
            self.assertEqual(client.post(path,base_url=self.base,json={}).status_code,403)
            for origin,token in [('https://evil.example',anon),(self.base,'wrong')]:
                self.assertEqual(client.post(path,base_url=self.base,headers={'Origin':origin,'X-LifeOS-Token':token},json={}).status_code,403)
        for path in ['/logout','/api/entries','/api/repayments','/api/reminders']:
            self.assertEqual(self.client.post(path,base_url=self.base,json={}).status_code,403)
        self.assertEqual(self.post(self.client,'/api/entries',{},anon).status_code,403)

    def test_session_rotation_hashing_restart_and_logout(self):
        client=self.app.test_client()
        csrf=self.token(client,'/login')
        old=client.get_cookie(COOKIE,domain='pocket.example.com').value
        response=self.post(client,'/login',dict(username='TESTER',password=self.password),csrf)
        token=client.get_cookie(COOKIE,domain='pocket.example.com').value
        self.assertNotEqual(old,token)
        self.assertIsNone(self.accounts.session(old))
        for flag in ['Secure','HttpOnly','SameSite=Lax','Path=/','Max-Age=604800']:
            self.assertIn(flag,response.headers['Set-Cookie'])
        with self.accounts.connect() as conn:
            stored=str([tuple(r) for r in conn.execute('SELECT * FROM sessions')])
        self.assertNotIn(token,stored)
        self.assertIn(digest(token),stored)
        restarted=create_app(self.settings).test_client()
        restarted.set_cookie(COOKIE,token,domain='pocket.example.com')
        self.assertEqual(self.get(restarted,'/api/state').status_code,200)
        self.assertEqual(self.post(restarted,'/logout',{}).status_code,200)
        self.assertIsNone(restarted.get_cookie(COOKIE,domain='pocket.example.com'))
        self.assertEqual(self.get(client,'/api/state').status_code,401)

    def test_expiry(self):
        with self.accounts.connect() as conn: conn.execute('UPDATE sessions SET expires_at=0')
        self.assertEqual(self.get(self.client,'/api/state').status_code,401)
        self.assertEqual(self.get(self.client,'/').location,'/login')

    def test_signup_validation_and_empty_account(self):
        other=self.signup('Mixed.Case-Name_1')
        state=self.get(other,'/api/state').json
        self.assertEqual(state['entries'],[])
        self.assertEqual(state['repayments'],[])
        self.assertFalse(state['account']['isOwner'])
        client=self.app.test_client()
        csrf=self.token(client,'/signup')
        for username,password,confirmation,status in [
            ('mixed.case-name_1',self.password,self.password,409),
            ('../../owner',self.password,self.password,400),
            ('new','short','short',400),('new',self.password,'different',400),
            (['invalid'],self.password,self.password,400)]:
            self.assertEqual(self.post(client,'/signup',dict(username=username,password=password,confirmation=confirmation),csrf).status_code,status)

    def test_records_exports_and_obsidian_isolated(self):
        entry=self.save(kind='loan',person='Owner borrower',amount='100',dueAt='2026-10-22T09:00').json['entries'][0]
        self.post(self.client,'/api/repayments',dict(entryId=entry['id'],amount='20',date='2026-10-21',notes='Owner repayment'))
        note=self.root/'vault/ai zone/openai agent/Personal/Finance/Expense Ledger.md'
        original=note.read_bytes()
        other=self.signup()
        other_entry=self.save(other,title='Other secret purchase',kind='loan',person='Other borrower',amount='80',dueAt='2026-10-23T09:00').json['entries'][0]
        self.post(other,'/api/repayments',dict(entryId=other_entry['id'],amount='30',date='2026-10-21',notes='Other repayment'))
        self.post(other,'/api/reminders',dict(id=other_entry['id'],done=True))
        self.assertEqual(note.read_bytes(),original)
        for actor,own,foreign in [(self.client,entry,other_entry),(other,other_entry,entry)]:
            for path in ['/api/state','/api/export.json','/api/ledger.md']:
                response=self.get(actor,path+'?accountId=ignored')
                self.assertEqual(response.status_code,200)
                self.assertIn(own['person'],response.text)
                self.assertNotIn(foreign['person'],response.text)
            self.assertEqual(self.get(actor,'/api/reminder.ics?id='+foreign['id']).status_code,404)
            self.assertEqual(self.get(actor,'/api/reminder.ics?id='+own['id']).status_code,200)
            self.assertEqual(self.save(actor,id=foreign['id'],accountId='ignored').status_code,400)
            self.assertEqual(self.post(actor,'/api/reminders',dict(id=foreign['id'],done=True)).status_code,400)
            self.assertEqual(self.post(actor,'/api/repayments',dict(entryId=foreign['id'],amount='1',date='2026-10-21')).status_code,400)
        state=self.get(other,'/api/state').json
        self.assertEqual(len(state['entries']),1)
        self.assertEqual(len(state['repayments']),1)
        self.assertEqual(state['repayments'][0]['amountMinor'],3000)
        self.assertNotIn(str(self.root),json.dumps(state))
        self.assertNotIn('Other secret purchase',note.read_text())
        self.assertNotIn('second-user',note.read_text())

    def test_migration_preserves_legacy_records_and_imports_credentials_once(self):
        legacy=self.root/'legacy'
        ledger=Ledger(legacy,'')
        entry=dict(kind='loan',title='Existing loan',person='Existing borrower',amount='100',date='2026-10-20',dueAt='2026-10-22T09:00')
        loan=ledger.save_entry(entry)['entries'][0]
        ledger.repay(dict(entryId=loan['id'],amount='10',date='2026-10-21'))
        before=ledger.state()
        raw=(legacy/'ledger.sqlite3').read_bytes()
        settings={**self.settings,'POCKET_DATA_DIR':str(legacy),'POCKET_VAULT':str(self.root/'legacy-vault')}
        app=create_app(settings)
        self.auth.write_text(json.dumps({'username':'replacement','passwordHash':self.password_hash}))
        client=create_app(settings).test_client()
        self.assertEqual(self.login(client).status_code,200)
        result=self.get(client,'/api/state').json
        self.assertEqual(result['entries'],before['entries'])
        self.assertEqual(result['repayments'],before['repayments'])
        self.assertEqual((legacy/'ledger.sqlite3').read_bytes(),raw)
        with app.extensions['pocket_accounts'].connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
        self.assertEqual(json.loads(self.auth.read_text())['username'],'replacement')

    def test_nonstandard_legacy_owner_username(self):
        self.auth.write_text(json.dumps({'username':'Owner name with spaces!','passwordHash':self.password_hash}))
        client=create_app({**self.settings,'POCKET_DATA_DIR':str(self.root/'legacy')}).test_client()
        self.assertEqual(self.login(client,'Owner name with spaces!').status_code,200)

    def test_missing_owner_fails_closed_and_migrated_owner_ignores_legacy_file(self):
        self.auth.unlink()
        create_app(self.settings)
        with self.assertRaises(RuntimeError): create_app({**self.settings,'POCKET_DATA_DIR':str(self.root/'fresh')})
        with sqlite3.connect(self.root/'fresh/accounts.sqlite3') as conn:
            conn.execute('INSERT INTO users VALUES (?,?,?,?,0,0)',('orphan','orphan','orphan',self.password_hash))
        with self.assertRaises(RuntimeError): create_app({**self.settings,'POCKET_DATA_DIR':str(self.root/'fresh')})

    def test_reset_revokes_only_selected_account(self):
        other=self.signup()
        self.accounts.reset_password('tester',self.password+'-changed')
        self.assertEqual(self.get(self.client,'/api/state').status_code,401)
        self.assertEqual(self.get(other,'/api/state').status_code,200)
        client=create_app(self.settings).test_client()
        self.assertEqual(self.login(client).status_code,401)
        self.assertEqual(self.login(client,password=self.password+'-changed').status_code,200)

    def test_login_limit_survives_restart_and_expires(self):
        client=self.app.test_client()
        csrf=self.token(client,'/login')
        for _ in range(10): self.assertEqual(self.post(client,'/login',dict(username='tester',password='wrong'),csrf).status_code,401)
        client=create_app(self.settings).test_client()
        response=self.login(client)
        self.assertEqual(response.status_code,429)
        self.assertIn('Retry-After',response.headers)
        with self.accounts.connect() as conn: conn.execute('UPDATE attempts SET happened_at=happened_at-301')
        self.assertEqual(self.login(client).status_code,200)

    def test_signup_limit_survives_restart(self):
        for i in range(5): self.signup('user-'+str(i))
        client=create_app(self.settings).test_client()
        csrf=self.token(client,'/signup')
        data=dict(username='sixth',password=self.password,confirmation=self.password)
        self.assertEqual(self.post(client,'/signup',data,csrf).status_code,429)
        with self.accounts.connect() as conn: conn.execute('UPDATE attempts SET happened_at=happened_at-3601')
        self.assertEqual(self.post(client,'/signup',data,csrf).status_code,200)

    def test_backup_restore_accounts_records_and_revoke_sessions(self):
        first=self.save().json
        other=self.signup()
        second=self.save(other,title='Another record').json
        snapshot=make_backup(self.root/'data',self.root/'backups')
        restored=self.root/'restored'
        restore(snapshot,restored)
        app=create_app({**self.settings,'POCKET_DATA_DIR':str(restored),'POCKET_VAULT':str(self.root/'restored-vault')})
        for username,expected in [('tester',first),('second-user',second)]:
            client=app.test_client()
            self.assertEqual(self.login(client,username).status_code,200)
            state=self.get(client,'/api/state').json
            self.assertEqual(state['entries'],expected['entries'])
            self.assertEqual(state['account'],expected['account'])
        old=other.get_cookie(COOKIE,domain='pocket.example.com').value
        self.assertIsNone(app.extensions['pocket_accounts'].session(old))
        with self.assertRaises(ValueError): restore(snapshot,restored)
        self.assertNotIn('Another record',(self.root/'restored-vault/ai zone/openai agent/Personal/Finance/Expense Ledger.md').read_text())

    def test_legacy_backup_and_incomplete_snapshot(self):
        Ledger(self.root/'old','')
        snapshot=make_backup(self.root/'old',self.root/'backups')
        restore(snapshot,self.root/'old-restored')
        (snapshot/'manifest.json').unlink()
        with self.assertRaises(OSError): restore(snapshot,self.root/'invalid')

    def test_private_files_and_nested_vault_rejected(self):
        for path in ['/auth.json','/accounts.sqlite3','/data/ledger.sqlite3','/config.json','/production.py','/accounts/anything/export']:
            self.assertEqual(self.get(self.client,path).status_code,404)
        for vault in [self.root/'data',self.root/'data/accounts',self.root/'data/accounts/nested']:
            with self.assertRaises(RuntimeError): create_app({**self.settings,'POCKET_VAULT':str(vault)})

    def test_repayment_limits_and_calendar_timezone(self):
        entry=self.save(kind='loan',person='Borrower',amount='100',dueAt='2026-10-22T09:00').json['entries'][0]
        data=dict(entryId=entry['id'],amount='40',date='2026-10-21',notes='')
        self.assertEqual(self.post(self.client,'/api/repayments',data).status_code,200)
        self.assertEqual(self.post(self.client,'/api/repayments',{**data,'amount':'61'}).status_code,400)
        self.assertIn('DTSTART:20261022T033000Z',self.get(self.client,'/api/reminder.ics?id='+entry['id']).text)

    def test_full_outstanding_repayment_is_recorded_and_cannot_duplicate(self):
        entry=self.save(kind='loan',person='Borrower',amount='100').json['entries'][0]
        self.assertEqual(self.post(self.client,'/api/repayments',dict(entryId=entry['id'],amount='25',date='2026-10-26',notes='Partial')).status_code,200)
        completed=self.post(self.client,'/api/repayments',dict(entryId=entry['id'],amount='75',date='2026-10-27',notes='Marked fully repaid'))
        self.assertEqual(completed.status_code,200)
        repayments=[r for r in completed.json['repayments'] if r['entryId']==entry['id']]
        self.assertEqual(sum(r['amountMinor'] for r in repayments),10000)
        self.assertEqual(repayments[0]['notes'],'Marked fully repaid')
        duplicate=self.post(self.client,'/api/repayments',dict(entryId=entry['id'],amount='75',date='2026-10-27',notes='Marked fully repaid'))
        self.assertEqual(duplicate.status_code,400)
        self.assertIn('amount outstanding',duplicate.json['error'])

    def test_simultaneous_full_repayments_only_record_once(self):
        ledger=Ledger(self.root/'concurrent-repayment','')
        entry=ledger.save_entry(dict(kind='loan',title='Loan',person='Borrower',amount='100',date='2026-10-27'))['entries'][0]
        payload=dict(entryId=entry['id'],amount='100',date='2026-10-27',notes='Marked fully repaid')
        def attempt(_):
            try:
                ledger.repay(payload)
                return 'recorded'
            except ValueError:
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,range(2)))
        self.assertCountEqual(results,['recorded','rejected'])
        self.assertEqual(len(ledger.state()['repayments']),1)

    def test_owner_export_can_remain_disabled(self):
        app=create_app({**self.settings,'POCKET_VAULT':''})
        client=app.test_client()
        self.assertEqual(self.login(client).status_code,200)
        self.assertFalse(self.get(client,'/api/state').json['sync']['enabled'])
        self.assertEqual(self.get(client,'/api/ledger.md').status_code,503)


if __name__ == '__main__': unittest.main()

"""Recoverable, account-private moves preserve complete record graphs atomically."""
import datetime as dt
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from unittest.mock import patch

from deploy.backup import make_backup
from deploy.restore import restore
from production import create_app
from server import Ledger, remaining, page_destination
from test_production import ProductionFixture


class TrashTests(ProductionFixture, unittest.TestCase):
    def move(self, identifier, client=None):
        response=self.post(client or self.client,'/api/trash',{'id':identifier})
        self.assertEqual(response.status_code,200,response.json)
        return response.json

    def undo(self, identifier, client=None):
        response=self.post(client or self.client,'/api/restore',{'id':identifier})
        self.assertEqual(response.status_code,200,response.json)
        return response.json

    def test_each_type_moves_and_restores_original_payload_and_completed_trip(self):
        trip=self.post(self.client,'/api/trips',{'name':'Goa'}).json['trips'][0]
        self.post(self.client,'/api/trip-status',{'id':trip['id'],'action':'complete'})
        for kind in ['expense','loan','reminder']:
            with self.subTest(kind=kind):
                entry=self.save(kind=kind,title='Original '+kind,person='Maya',tripId=trip['id'],dueAt='2026-01-01T09:00',notes='Keep this note').json['entries'][0]
                if kind=='reminder':
                    state=self.post(self.client,'/api/reminders',{'id':entry['id'],'done':True}).json
                    entry=next(e for e in state['entries'] if e['id']==entry['id'])
                state=self.move(entry['id'])
                self.assertNotIn(entry['id'],[e['id'] for e in state['entries']])
                archived=state['trash'][0]
                self.assertEqual(archived['entry'],entry)
                self.assertEqual(archived['repayments'],[])
                self.assertIsNone(archived['mandatePayment'])
                self.assertTrue(dt.datetime.fromisoformat(archived['trashedAt']).tzinfo)
                self.assertEqual(self.get(self.client,'/api/reminder.ics?id='+entry['id']).status_code,404)
                note=self.get(self.client,'/api/ledger.md').text
                self.assertNotIn(entry['title'],note)
                restored=self.undo(entry['id'])
                self.assertEqual(next(e for e in restored['entries'] if e['id']==entry['id']),entry)
                self.assertEqual(restored['trash'],[])
                self.assertEqual(self.get(self.client,'/api/reminder.ics?id='+entry['id']).status_code,200)
                self.assertIn(entry['title'],self.get(self.client,'/api/ledger.md').text)

    def test_partial_and_fully_repaid_loans_move_complete_history(self):
        for paid in [200,500]:
            loan=self.save(kind='loan',person='Maya',amount='500',dueAt='2026-11-01T09:00').json['entries'][0]
            state=self.post(self.client,'/api/repayments',dict(entryId=loan['id'],amount=str(paid),date='2026-10-28',notes='Received')).json
            history=[r for r in state['repayments'] if r['entryId']==loan['id']]
            moved=self.move(loan['id'])
            self.assertEqual(moved['trash'][0]['repayments'],history)
            self.assertFalse(any(r['entryId']==loan['id'] for r in moved['repayments']))
            for path,body in [('/api/repayments',dict(entryId=loan['id'],amount='1',date='2026-10-28')),('/api/reminders',{'id':loan['id'],'done':True})]:
                self.assertEqual(self.post(self.client,path,body).status_code,400)
            self.assertEqual(self.save(id=loan['id'],title='Stale edit').status_code,400)
            restored=self.undo(loan['id'])
            self.assertEqual(remaining(loan,restored['repayments']),(500-paid)*100)
            self.assertEqual([r for r in restored['repayments'] if r['entryId']==loan['id']],history)

    @patch('server.today',return_value=dt.date(2026,10,28))
    def test_paused_mandate_reversal_repayment_and_restore_conflict(self,current):
        mandate=self.post(self.client,'/api/mandates',dict(title='Netflix',amount='499',firstDueDate='2026-10-31',category='Other')).json['mandates'][0]
        request={'id':mandate['id'],'period':'2026-10'}
        paid=self.post(self.client,'/api/mandate-payments',request).json
        original=paid['entries'][0]; original_occurrence=paid['mandates'][0]['occurrences'][0]
        self.post(self.client,'/api/mandate-status',{'id':mandate['id'],'paused':True})
        state=self.move(original['id'])
        occurrence=state['mandates'][0]['occurrences'][0]
        self.assertEqual(occurrence['status'],'unpaid');self.assertTrue(occurrence['paymentReversed'])
        self.assertIsNotNone(state['mandates'][0]['activeWindows'][-1]['to'])
        with patch('server.today',return_value=dt.date(2026,11,2)):
            future=self.get(self.client,'/api/state').json
            self.assertEqual([o['period'] for o in future['mandates'][0]['occurrences']],['2026-10'])
            self.assertEqual(self.post(self.client,'/api/mandate-payments',{'id':mandate['id'],'period':'2026-11'}).status_code,400)
        # Original payment can be restored, including the original paidAt.
        restored=self.undo(original['id'])
        self.assertEqual(restored['entries'],[original])
        self.assertEqual(restored['mandates'][0]['occurrences'][0],original_occurrence)
        self.move(original['id'])
        replacement=self.post(self.client,'/api/mandate-payments',request)
        self.assertEqual(replacement.status_code,200,replacement.json)
        replacement_id=replacement.json['entries'][0]['id']
        self.assertNotEqual(replacement_id,original['id'])
        conflict=self.post(self.client,'/api/restore',{'id':original['id']})
        self.assertEqual(conflict.status_code,400);self.assertIn('replacement payment',conflict.json['error'])
        unchanged=self.get(self.client,'/api/state').json
        self.assertEqual(len(unchanged['entries']),1);self.assertEqual(len(unchanged['trash']),1)
        self.move(replacement_id)
        restored=self.undo(original['id'])
        self.assertEqual(restored['entries'],[original]);self.assertEqual(len(restored['trash']),1)
        self.assertEqual(restored['mandates'][0]['occurrences'][0]['status'],'paid')
        self.assertEqual(self.post(self.client,'/api/restore',{'id':replacement_id}).status_code,400)

    def test_repeated_requests_and_invalid_ids_do_not_duplicate_or_cross_accounts(self):
        entry=self.save().json['entries'][0];other=self.signup()
        for path in ['/api/trash','/api/restore']:
            self.assertEqual(self.post(other,path,{'id':entry['id'],'accountId':self.get(self.client,'/api/state').json['account']['id']}).status_code,400)
            for bad in [None,[],{},'']:
                self.assertEqual(self.post(self.client,path,{'id':bad}).status_code,400)
        self.move(entry['id']);state=self.move(entry['id'])
        self.assertEqual(len(state['trash']),1)
        self.assertEqual(self.get(other,'/api/state').json['trash'],[])
        self.assertNotIn(entry['id'],self.get(other,'/api/export.json').text)
        self.assertEqual(self.post(other,'/api/restore',{'id':entry['id']}).status_code,400)
        self.undo(entry['id']);state=self.undo(entry['id'])
        self.assertEqual(state['entries'],[entry]);self.assertEqual(state['trash'],[])
        # Neither trip definitions nor mandate definitions are deletable through this API.
        trip=self.post(self.client,'/api/trips',{'name':'Goa'}).json['trips'][0]
        self.assertEqual(self.post(self.client,'/api/trash',{'id':trip['id']}).status_code,400)

    def test_authentication_csrf_and_login_signup_return_to_trash(self):
        anon=self.app.test_client();response=self.get(anon,'/trash')
        self.assertEqual((response.status_code,response.location),(303,'/login?next=/trash'))
        self.assertNotIn('WWW-Authenticate',response.headers)
        self.assertEqual(page_destination('/trash'),'/trash')
        for auth in ['/login','/signup']:
            self.assertIn('action="'+auth+'?next=/trash"',self.get(anon,auth+'?next=/trash').text)
        response=self.post(anon,'/login?next=/trash',dict(username='tester',password=self.password),self.token(anon,'/login?next=/trash'))
        self.assertEqual(response.json['redirect'],'/trash')
        response=self.get(anon,'/trash');self.assertEqual(response.status_code,200)
        self.assertIn('data-pocket-view="trash"',response.text);self.assertEqual(response.headers['Cache-Control'],'no-store')
        for path in ['/api/trash','/api/restore']:
            self.assertEqual(self.client.post(path,base_url=self.base,json={'id':'x'}).status_code,403)
            self.assertEqual(self.app.test_client().post(path,base_url=self.base,json={}).status_code,401)
        for bad in ['/trash/..','//trash','https://evil.example/trash','/trash?next=//evil']:
            self.assertEqual(page_destination(bad),'/')

    def test_owner_only_endpoint_omits_trash_and_recovers_after_restore(self):
        owner=self.save(title='Owner deleted expense').json['entries'][0]
        other=self.signup();private=self.save(other,title='Other private expense').json['entries'][0]
        self.move(private['id'],other);self.move(owner['id'])
        token=self.post(self.client,'/api/obsidian/token',{}).json['token']
        def download():
            return self.app.test_client().get('/api/obsidian/ledger.md',base_url=self.base,headers={'Authorization':'Bearer '+token})
        response=download();self.assertEqual(response.status_code,200)
        self.assertNotIn('Owner deleted expense',response.text);self.assertNotIn('Other private expense',response.text)
        self.undo(owner['id']);self.assertIn('Owner deleted expense',download().text)
        self.assertNotIn('Other private expense',download().text)

    def test_backup_restore_includes_trash_for_each_account(self):
        owner=self.save(kind='loan',person='Maya',amount='500').json['entries'][0]
        self.post(self.client,'/api/repayments',dict(entryId=owner['id'],amount='200',date='2026-10-28'))
        self.move(owner['id'])
        other=self.signup();private=self.save(other,title='Private record').json['entries'][0];self.move(private['id'],other)
        backup=make_backup(self.root/'data',self.root/'backups');target=self.root/'restored';restore(backup,target)
        app=create_app({**self.settings,'POCKET_DATA_DIR':str(target),'POCKET_VAULT':str(self.root/'restored-vault')})
        for username,entry in [('tester',owner),('second-user',private)]:
            client=app.test_client();self.login(client,username)
            state=self.get(client,'/api/export.json').json
            self.assertEqual(state['schemaVersion'],4);self.assertEqual(state['entries'],[])
            self.assertEqual(state['trash'][0]['entry'],entry)
            state=self.undo(entry['id'],client)
            self.assertEqual(state['entries'],[entry]);self.assertEqual(state['trash'],[])
            if username=='tester':self.assertEqual(remaining(entry,state['repayments']),30000)


class TrashStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.ledger=Ledger(self.root,'')
    def tearDown(self): self.temp.cleanup()
    def loan(self):
        return self.ledger.save_entry(dict(kind='loan',title='Loan',person='Maya',amount='500',date='2026-10-28'))['entries'][0]
    def test_upgrade_is_additive_and_repeatable(self):
        entry=self.loan()
        with self.ledger.connect() as conn:conn.execute('DROP TABLE trash')
        for _ in range(2):
            state=Ledger(self.root,'').state();self.assertEqual(state['entries'],[entry]);self.assertEqual(state['trash'],[])
    def test_concurrent_repayment_and_trash_preserve_or_reject_payment_atomically(self):
        entry=self.loan();other=Ledger(self.root,'')
        def repay():
            try:other.repay(dict(entryId=entry['id'],amount='200',date='2026-10-28'));return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            payment=pool.submit(repay);move=pool.submit(self.ledger.trash_entry,{'id':entry['id']})
            paid=payment.result();move.result()
        state=self.ledger.state()
        self.assertEqual(state['entries'],[]);self.assertEqual(state['repayments'],[])
        self.assertEqual(len(state['trash'][0]['repayments']),int(paid))
        state=self.ledger.restore_entry({'id':entry['id']})
        self.assertEqual(remaining(entry,state['repayments']),30000 if paid else 50000)
    def test_concurrent_duplicate_moves_and_restores_are_single_records(self):
        entry=self.loan();ledgers=[self.ledger,Ledger(self.root,'')]
        for method in ['trash_entry','restore_entry']:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda ledger:getattr(ledger,method)({'id':entry['id']}),ledgers))
            state=self.ledger.state()
            self.assertEqual(len(state['trash']) if method=='trash_entry' else len(state['entries']),1)
        self.assertEqual(self.ledger.state()['trash'],[])
    @patch('server.today',return_value=dt.date(2026,10,28))
    def test_concurrent_restore_and_replacement_mandate_payment_never_duplicate(self,current):
        mandate=self.ledger.save_mandate(dict(title='Subscription',amount='499',firstDueDate='2026-10-01',category='Other'))['mandates'][0]
        request={'id':mandate['id'],'period':'2026-10'}
        entry=self.ledger.pay_mandate(request)['entries'][0];self.ledger.trash_entry({'id':entry['id']})
        other=Ledger(self.root,'')
        def restore_original():
            try:self.ledger.restore_entry({'id':entry['id']});return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            undo=pool.submit(restore_original);replacement=pool.submit(other.pay_mandate,request)
            restored=undo.result();replacement.result()
        state=self.ledger.state()
        self.assertEqual(len(state['entries']),1)
        self.assertEqual(len(state['trash']),0 if restored else 1)
        self.assertEqual(state['mandates'][0]['occurrences'][0]['status'],'paid')
        with self.ledger.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM mandate_payments').fetchone()[0],1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_restore_failure_keeps_full_history_in_trash(self):
        entry=self.loan()
        for amount in ['100','200']:
            self.ledger.repay(dict(entryId=entry['id'],amount=amount,date='2026-10-28',notes='Keep repayment '+amount))
        original=self.ledger.state();self.ledger.trash_entry({'id':entry['id']});before=self.ledger.state()
        with self.ledger.connect() as conn:
            conn.execute("CREATE TRIGGER reject_repayment BEFORE INSERT ON repayments BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.ledger.restore_entry({'id':entry['id']})
        after=self.ledger.state()
        for key in ['entries','repayments','trash']:self.assertEqual(after[key],before[key])
        with self.ledger.connect() as conn:conn.execute('DROP TRIGGER reject_repayment')
        restored=self.ledger.restore_entry({'id':entry['id']})
        for key in ['entries','repayments']:self.assertEqual(restored[key],original[key])

    def test_storage_error_rolls_back_the_whole_move(self):
        entry=self.loan();self.ledger.repay(dict(entryId=entry['id'],amount='200',date='2026-10-28'))
        before=self.ledger.state()
        with self.ledger.connect() as conn:
            conn.execute("CREATE TRIGGER reject_deletion BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.ledger.trash_entry({'id':entry['id']})
        after=self.ledger.state()
        for key in ['entries','repayments','trash']:self.assertEqual(after[key],before[key])


class LocalTrashRoutes(unittest.TestCase):
    def test_local_trash_pages_mutations_csrf_and_restore(self):
        import server
        with tempfile.TemporaryDirectory() as directory:
            handlers=[]
            class FakeServer:
                def __init__(self,address,handler):handlers.append(handler)
                def serve_forever(self):pass
            with patch.object(server,'ThreadingHTTPServer',FakeServer),patch('sys.argv',['server.py','--port','8769','--data-dir',directory,'--vault','']),redirect_stdout(StringIO()):server.main()
            handler=object.__new__(handlers[0]);responses=[]
            handler.reply=lambda status,body,*args,**kwargs:responses.append((status,body))
            handler.headers={'Host':'127.0.0.1:8769'}
            def get(path):handler.path=path;handler.do_GET();return responses.pop()
            status,page=get('/trash');self.assertEqual(status,200);self.assertIn(b'data-pocket-view="trash"',page)
            token=re.search(rb'name="lifeos-token" content="([^"]+)"',page)[1].decode()
            def post(path,body,csrf=token):
                raw=json.dumps(body).encode();handler.path=path;handler.rfile=BytesIO(raw)
                handler.headers.update({'X-LifeOS-Token':csrf,'Content-Length':str(len(raw))});handler.do_POST();return responses.pop()
            status,state=post('/api/entries',dict(kind='expense',title='Dinner',amount='200',date='2026-10-28'))
            identifier=state['entries'][0]['id']
            self.assertEqual(post('/api/trash',{'id':identifier},'bad')[0],403)
            self.assertEqual(len(post('/api/trash',{'id':identifier})[1]['trash']),1)
            self.assertEqual(len(post('/api/restore',{'id':identifier})[1]['entries']),1)


if __name__=='__main__':unittest.main()

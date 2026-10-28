"""Trips use one account ledger; exercise migrations, ownership, exports and auth."""
import json
from io import BytesIO
from email.message import Message
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from deploy.backup import make_backup
from deploy.restore import restore
from deploy import sync_obsidian as sync
from production import create_app
from server import Ledger, page_destination, remaining
from test_production import ProductionFixture


class TripTests(ProductionFixture, unittest.TestCase):
    def trip(self, client=None, **fields):
        response = self.post(client or self.client, '/api/trips', {'name':'Goa', **fields})
        self.assertEqual(response.status_code, 200, response.json)
        return next(t for t in response.json['trips'] if t['id'] == fields.get('id', response.json['trips'][0]['id']))

    def status(self, trip, action, client=None):
        response = self.post(client or self.client, '/api/trip-status', {'id':trip['id'], 'action':action})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_create_edit_validation_and_repeatable_legacy_upgrade(self):
        ledger = Ledger(self.root/'legacy', '')
        ledger.save_entry(dict(kind='expense', title='Old record', amount='20', date='2026-01-01'))
        with ledger.connect() as conn:
            # Recreate a pre-trips ledger without a tripId in its JSON.
            entry = json.loads(conn.execute('SELECT payload FROM entries').fetchone()[0])
            entry.pop('tripId', None)
            conn.execute('UPDATE entries SET payload=?', (json.dumps(entry),))
            conn.execute('DROP TABLE trips'); conn.execute('DROP TABLE settings')
        for _ in range(2):
            state = Ledger(self.root/'legacy', '').state()
            self.assertEqual(state['entries'], [entry])
            self.assertEqual(state['trips'], [])
            self.assertIsNone(state['activeTripId'])
        trip = self.trip(destination='Goa', startDate='2026-11-01', endDate='2026-11-08')
        edited = self.trip(id=trip['id'], name='Goa holiday', destination='Panaji')
        self.assertEqual(edited['createdAt'], trip['createdAt'])
        self.assertEqual(edited['name'], 'Goa holiday')
        self.assertEqual(len(self.get(self.client, '/api/state').json['trips']), 1)
        for body in [{'name':''}, {'name':'x'*101}, {'name':'Goa','startDate':'2026-02-30'}, {'name':'Goa','startDate':'2026-11-10','endDate':'2026-11-01'}]:
            self.assertEqual(self.post(self.client, '/api/trips', body).status_code, 400)

    def test_active_default_shared_and_completion_does_not_change_records(self):
        goa = self.trip(); vizag = self.trip(name='Vizag')
        self.assertEqual(self.status(goa, 'activate')['activeTripId'], goa['id'])
        loan = self.save(kind='loan',person='Maya',amount='500',tripId=goa['id'],dueAt='2026-11-10T09:00').json['entries'][0]
        self.assertEqual(self.status(vizag, 'activate')['activeTripId'], vizag['id'])
        # A stale request to clear an old default must not clear the new active trip.
        self.assertEqual(self.status(goa, 'deactivate')['activeTripId'], vizag['id'])
        state = self.status(vizag, 'complete')
        self.assertIsNone(state['activeTripId'])
        self.assertEqual(state['entries'], [loan])
        self.assertEqual(state['repayments'], [])
        self.assertEqual(self.post(self.client, '/api/trip-status', {'id':vizag['id'],'action':'activate'}).status_code,400)
        self.status(vizag, 'reopen')
        self.status(vizag, 'activate')
        self.assertIsNone(self.status(vizag, 'deactivate')['activeTripId'])
        self.status(goa, 'activate')
        restart = create_app(self.settings).test_client()
        self.login(restart)
        self.assertEqual(self.get(restart,'/api/state').json['activeTripId'],goa['id'])
        self.status(goa, 'complete')
        self.assertEqual(self.save(tripId=goa['id'],date='2027-01-01').status_code,200)

    def test_old_clients_preserve_assignments_but_never_inherit_active_trip(self):
        goa = self.trip(); vizag = self.trip(name='Vizag')
        self.status(goa, 'activate')
        response = self.save()
        self.assertIsNone(response.json['entries'][0]['tripId'])
        entry = self.save(tripId=goa['id']).json['entries'][0]
        self.assertEqual(self.save(id=entry['id'],title='Legacy edit').json['entries'][0]['tripId'], goa['id'])
        self.assertEqual(self.save(id=entry['id'],tripId=vizag['id']).json['entries'][0]['tripId'], vizag['id'])
        self.assertIsNone(self.save(id=entry['id'],tripId=None).json['entries'][0]['tripId'])
        self.assertEqual(len(self.get(self.client,'/api/state').json['entries']),2)

    def test_accounts_cannot_read_edit_activate_or_assign_foreign_trips(self):
        goa = self.trip(); other = self.signup(); private = self.trip(other,name='Secret trip')
        owner_state = self.get(self.client,'/api/state').json
        self.assertNotIn(private['id'], json.dumps(owner_state))
        for client, foreign in [(other,goa),(self.client,private)]:
            for path, data in [('/api/trips',{'id':foreign['id'],'name':'Hijacked'}),('/api/trip-status',{'id':foreign['id'],'action':'activate'})]:
                self.assertEqual(self.post(client,path,data).status_code,400)
            self.assertEqual(self.save(client,tripId=foreign['id'],accountId=owner_state['account']['id']).status_code,400)
            self.assertEqual(self.get(client,'/trips/'+foreign['id']).status_code,404)
        self.assertNotIn(private['name'],self.get(self.client,'/api/export.json').text)
        self.assertNotIn(goa['name'],self.get(other,'/api/ledger.md').text)

    def test_csrf_private_routes_and_safe_login_returns(self):
        trip = self.trip(); path='/trips/'+trip['id']; anon=self.app.test_client()
        for page in ['/trips',path]:
            response=self.get(anon,page)
            self.assertEqual((response.status_code,response.location),(303,'/login?next='+page))
            self.assertNotIn('WWW-Authenticate',response.headers)
            auth=self.get(anon,'/login?next='+page)
            self.assertIn('action="/login?next='+page+'"',auth.text)
            self.assertIn('href="/signup?next='+page+'"',auth.text)
            self.assertEqual(self.get(self.client,page).headers['Cache-Control'],'no-store')
        token=self.token(anon,'/login?next='+path)
        response=self.post(anon,'/login?next='+path,dict(username='tester',password=self.password),token)
        self.assertEqual(response.json['redirect'],path)
        new=self.app.test_client(); token=self.token(new,'/signup?next=/trips')
        response=self.post(new,'/signup?next=/trips',dict(username='tripper',password=self.password,confirmation=self.password),token)
        self.assertEqual(response.json['redirect'],'/trips')
        self.assertEqual(self.get(new,'/api/state').json['trips'],[])
        for unsafe in ['https://evil.test','//evil.test','/trips/../logout','/trips/%2f%2fevil','/trips/'+trip['id']+'?next=evil','/trips/'+trip['id']+'#fragment','/trips/<script>']:
            self.assertEqual(page_destination(unsafe),'/')
        for route in ['/api/trips','/api/trip-status']:
            self.assertEqual(self.client.post(route,base_url=self.base,json={}).status_code,403)
            self.assertEqual(self.app.test_client().post(route,base_url=self.base,json={}).status_code,401)
        self.assertEqual(self.get(self.client,'/trips/not-a-uuid').status_code,404)

    def test_cross_month_bookings_repayments_and_reminder_history_stay_single_records(self):
        trip=self.trip(startDate='2026-11-01',endDate='2026-11-10')
        self.save(title='Advance flight',amount='3000',date='2026-09-01',tripId=trip['id'])
        self.save(title='Late receipt',amount='100',date='2026-12-01',tripId=trip['id'])
        loan=self.save(kind='loan',title='Hotel deposit',person='Maya',amount='500',tripId=trip['id'],dueAt='2026-12-02T09:00').json['entries'][0]
        reminder=self.save(kind='reminder',title='Call hotel',tripId=trip['id'],dueAt='2026-11-01T09:00').json['entries'][0]
        self.status(trip,'complete')
        self.post(self.client,'/api/repayments',dict(entryId=loan['id'],amount='200',date='2026-12-02',notes='Partial'))
        state=self.get(self.client,'/api/state').json
        self.assertEqual(remaining(loan,state['repayments']),30000)
        self.post(self.client,'/api/repayments',dict(entryId=loan['id'],amount='300',date='2026-12-03',notes='Marked fully repaid'))
        self.post(self.client,'/api/reminders',dict(id=reminder['id'],done=True))
        state=self.get(self.client,'/api/state').json
        self.assertEqual(len(state['entries']),4)
        self.assertTrue(all(e['tripId']==trip['id'] for e in state['entries']))
        self.assertEqual(sum(e['amountMinor'] for e in state['entries'] if e['kind']=='expense'),310000)
        self.assertEqual(remaining(loan,state['repayments']),0)
        self.assertEqual(len(state['repayments']),2)
        self.assertTrue(next(e for e in state['entries'] if e['id']==reminder['id'])['reminderDone'])
        self.assertEqual(self.post(self.client,'/api/repayments',dict(entryId=loan['id'],amount='300',date='2026-12-03')).status_code,400)

    def test_mandates_never_inherit_trip_but_paid_expense_can_be_assigned(self):
        trip=self.trip(); self.status(trip,'activate')
        result=self.post(self.client,'/api/mandates',dict(title='Netflix',amount='499',firstDueDate='2026-01-01',category='Other',tripId=trip['id']))
        mandate=result.json['mandates'][0]
        paid=self.post(self.client,'/api/mandate-payments',dict(id=mandate['id'],period='2026-01')).json
        expense=paid['entries'][0]
        self.assertIsNone(expense.get('tripId'))
        edited=self.save(id=expense['id'],title=expense['title'],amount='499',date=expense['date'],tripId=trip['id']).json['entries'][0]
        self.assertEqual(edited['tripId'],trip['id'])
        self.assertEqual(edited['mandateId'],mandate['id'])
        again=self.post(self.client,'/api/mandate-payments',dict(id=mandate['id'],period='2026-01')).json
        self.assertEqual(len(again['entries']),1)
        self.assertEqual(again['entries'][0]['tripId'],trip['id'])

    def test_backup_restores_all_accounts_trips_assignments_and_defaults(self):
        goa=self.trip(); self.status(goa,'activate'); self.save(tripId=goa['id'])
        other=self.signup(); private=self.trip(other,name='Private'); self.status(private,'activate',other); self.save(other,tripId=private['id'])
        backup=make_backup(self.root/'data',self.root/'backups'); restored=self.root/'restored';restore(backup,restored)
        app=create_app({**self.settings,'POCKET_DATA_DIR':str(restored),'POCKET_VAULT':str(self.root/'restored-vault')})
        for name,trip in [('tester',goa),('second-user',private)]:
            client=app.test_client(); self.login(client,name)
            state=self.get(client,'/api/export.json').json
            self.assertEqual(state['trips'],[trip]);self.assertEqual(state['activeTripId'],trip['id'])
            self.assertEqual(state['entries'][0]['tripId'],trip['id'])
            self.assertEqual(state['schemaVersion'],3)

    def test_concurrent_active_updates_leave_exactly_one_default(self):
        ledger=Ledger(self.root/'concurrent','')
        ids=[ledger.save_trip({'name':str(i)})['trips'][0]['id'] for i in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda identifier:ledger.trip_status({'id':identifier,'action':'activate'}),ids))
        self.assertIn(ledger.state()['activeTripId'],ids)
        with ledger.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM settings WHERE key="activeTripId"').fetchone()[0],1)

    def test_owner_trip_notes_reach_isolated_sync_destination_only(self):
        trip=self.trip(name='Goa | holiday',destination='Panaji');self.status(trip,'activate')
        self.save(title='Trip dinner',amount='200',tripId=trip['id'])
        other=self.signup(); private=self.trip(other,name='Other private trip');self.save(other,title='Never exported',tripId=private['id'])
        issued=self.post(self.client,'/api/obsidian/token',{}).json
        vault=self.root/'mac-vault';vault.mkdir();config=self.root/'sync.json'
        sync.save_config(config,dict(version=1,origin=self.base,ownerId=issued['ownerId'],vault=str(vault)))
        app=self.app
        class Opener:
            def open(self,request,timeout):
                response=app.test_client().get(sync.ENDPOINT,base_url='https://pocket.example.com',headers=dict(request.header_items()))
                result=BytesIO(response.data);result.status=response.status_code;result.headers=Message()
                for key,value in response.headers.items(): result.headers[key]=value
                result.geturl=lambda:request.full_url
                return result
        with patch.object(sync,'mac_keychain') as keychain, patch.object(sync,'build_opener',return_value=Opener()):
            keychain.return_value.get_password.return_value=issued['token']
            self.assertIn('verified',sync.sync_once(config))
        note=(vault/sync.NOTE).read_text()
        self.assertIn('## Trips',note);self.assertIn('Goa \\| holiday',note);self.assertIn('Trip dinner',note)
        self.assertIn('Total expenses: ₹200.00',note)
        self.assertNotIn('Other private trip',note);self.assertNotIn('Never exported',note)
        self.assertNotIn(issued['token'],note)

class LocalTripRoutes(unittest.TestCase):
    def test_local_pages_csrf_assignments_and_exports_without_opening_a_port(self):
        import tempfile
        import server
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            handlers=[]
            class FakeServer:
                def __init__(self,address,handler):
                    handlers.append(handler)
                def serve_forever(self): pass
            with patch.object(server,'ThreadingHTTPServer',FakeServer), patch('sys.argv',['server.py','--port','8769','--data-dir',directory,'--vault','']), redirect_stdout(StringIO()):
                server.main()
            handler=object.__new__(handlers[0]); responses=[]
            handler.reply=lambda status,body,*args,**kwargs: responses.append((status,body))
            handler.headers={'Host':'127.0.0.1:8769'}
            def get(path):
                handler.path=path;handler.do_GET();return responses.pop()
            status,body=get('/trips')
            self.assertEqual(status,200);self.assertIn(b'data-pocket-view="trips"',body)
            import re
            csrf=re.search(rb'name="lifeos-token" content="([^"]+)"',body)[1].decode()
            def post(path,body,token=csrf):
                handler.path=path;raw=json.dumps(body).encode();handler.rfile=BytesIO(raw)
                handler.headers.update({'Content-Length':str(len(raw)),'X-LifeOS-Token':token})
                handler.do_POST();return responses.pop()
            self.assertEqual(post('/api/trips',{'name':'Goa'},'bad')[0],403)
            status,state=post('/api/trips',{'name':'Goa'});self.assertEqual(status,200)
            trip=state['trips'][0];identifier=trip['id']
            self.assertIn(b'data-trip-id="'+identifier.encode()+b'"',get('/trips/'+identifier)[1])
            self.assertEqual(get('/trips/11111111-1111-1111-1111-111111111111')[0],404)
            self.assertEqual(get('/trips/not-valid')[0],404)
            self.assertEqual(post('/api/trip-status',{'id':identifier,'action':'activate'})[1]['activeTripId'],identifier)
            self.assertEqual(post('/api/entries',dict(kind='expense',title='Dinner',amount='200',date='2026-10-28',tripId=identifier))[0],200)
            exported=json.loads(get('/api/export.json')[1]);self.assertEqual(exported['entries'][0]['tripId'],identifier)
            for path in ['/','/quick-entry','/app.js','/journal.js','/companion.js']:
                self.assertEqual(get(path)[0],200)
            handler.headers['Host']='untrusted.example';self.assertEqual(get('/trips')[0],403)


if __name__ == '__main__':
    unittest.main()

import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from . import graduation as g
from .graduation_service import result, handler
from worlds.engine import World


class GraduationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'cohort'
        with patch.object(g.inc,'command',return_value='pinned'):
            self.m=g.prepare(self.root,'image')

    def test_six_lives_finite_private_profile(self):
        self.assertEqual(len(self.m['arms']),6)
        self.assertEqual(self.m['cutoff']-self.m['started'],172800)
        self.assertEqual(self.m['capture_interval'],1800)
        self.assertFalse(self.m['external_application_writes'])
        self.assertEqual(sum(a['entry']=='inherited' for a in self.m['arms'].values()),3)
        for path in self.m['worlds'].values():
            with World(path).s.transaction() as db:
                cfg=World(path).s.meta(db,'config')
                self.assertEqual((cfg['model'],cfg['effort']),('gpt-5.6-terra','medium'))
                self.assertEqual(cfg['maximum_starts'],6)

    def test_inherited_contracts_paid_refundable_not_organic_or_renewing(self):
        for name,location in self.m['worlds'].items():
            w=World(location)
            with w.s.transaction() as db:
                cs=w.s.rows(db,'contract')
                self.assertEqual(len(cs),2 if name in g.INHERITED else 0)
                for c in cs:
                    self.assertEqual(c['captured'],1)
                    self.assertFalse(c['renew'])
                    self.assertIn('operator-seeded',c['terms']['terms'])
                    self.assertEqual(c['terms']['refund_seconds'],86400)

    def test_exposures_idempotent_and_no_fresh_purchase(self):
        at=self.m['started']+3601
        for name in g.INHERITED:
            w=World(self.m['worlds'][name])
            with w.s.transaction() as db:
                w.s.meta(db,'endpoints',{self.m['arms'][name]['subject']:{'host':'127.0.0.1','port':1}})
        with patch.object(g.time,'time',return_value=at):
            for name in self.m['worlds']:
                g.tick(self.root,name)
                w=World(self.m['worlds'][name])
                with w.s.transaction() as db:before=db.execute('SELECT count(*) FROM receipts').fetchone()[0]
                g.tick(self.root,name)
                with w.s.transaction() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM receipts').fetchone()[0],before)
                    plan=w.s.meta(db,f'graduation:{name}:1')
                    self.assertEqual(plan['status'],'delivered')
                    self.assertEqual(plan['artifact_action']['audience'],[self.m['arms'][name]['subject']])

    def test_closed_world_and_missed_windows(self):
        World(self.m['worlds']['keel']).freeze()
        with patch.object(g.time,'time',return_value=self.m['started']+7000):
            g.tick(self.root,'keel');g.tick(self.root,'harbor')
        w=World(self.m['worlds']['harbor'])
        with w.s.transaction() as db:self.assertEqual(w.s.meta(db,'graduation:harbor:1')['status'],'missed')

    def test_legacy_environment_correction_bounded_and_idempotent(self):
        from .graduation_environment_repair import correct
        g.save(self.root/'cohort.json',{**self.m,'status':'running'})
        w=World(self.m['worlds']['loom']);key='graduation:loom:9'
        msg=w.act('relay','legacy',{'op':'message','to':'frontier','thread':key,
                  'text':'We have made no purchase or support agreement.'})['result']
        with w.s.transaction() as db:
            w.s.meta(db,key,{'status':'delivered','message':msg['id'],'owner':'relay','artifact':'fixture'})
        with patch('time.time',return_value=self.m['started']+8*3600):
            self.assertEqual(correct(self.root),[])
        with patch('time.time',return_value=self.m['started']+9*3600+1):
            receipts=correct(self.root)
            self.assertEqual(len(receipts),1)
            self.assertEqual(correct(self.root),[])
        with w.s.transaction() as db:
            new=w.s.get(db,receipts[0]['result']['id'])
            self.assertIn('not final',new['text'])
            self.assertEqual(new['thread'],key)
            self.assertEqual(w.s.get(db,msg['id'])['text'],'We have made no purchase or support agreement.')
        with patch('time.time',return_value=self.m['cutoff']+1):
            self.assertEqual(correct(self.root),[])

    def test_loom_preserves_occasion_and_does_not_deny_purchase_history(self):
        w=World(self.m['worlds']['loom'])
        for hour,phrase in [(9,'not final'),(10,'supersedes'),(14,'proactive follow-ups')]:
            with patch.object(g.time,'time',return_value=self.m['started']+hour*3600+1):
                g.tick(self.root,'loom')
            with w.s.transaction() as db:
                plan=w.s.meta(db,f'graduation:loom:{hour}')
                message=w.s.get(db,plan['message'])['text']
            self.assertIn(phrase,message)
            self.assertNotIn('We have made no purchase',message)
            self.assertIn('does not create a new purchase',message)

    def test_provider_disruption_and_restoration_scoped_and_idempotent(self):
        self.assertIn((18,'repeat'),g.SCHEDULE)
        w=World(self.m['worlds']['weft']);actor=self.m['arms']['weft']['subject']
        endpoints={actor:{'host':'10.0.0.4','port':8000}}
        with w.s.transaction() as db:w.s.meta(db,'endpoints',endpoints)
        at=self.m['started']+17*3600+1
        for _ in range(2):g.provider_disruption(self.root,'weft',w,self.m,at)
        with w.s.transaction() as db:self.assertEqual(w.s.meta(db,'endpoints')[actor]['port'],8001)
        for _ in range(2):g.provider_disruption(self.root,'weft',w,self.m,at+7200)
        with w.s.transaction() as db:
            self.assertEqual(w.s.meta(db,'endpoints'),endpoints)
            self.assertEqual(w.s.meta(db,'graduation-provider-disruption')['status'],'restored')

    def test_all_inherited_requests_and_incomplete(self):
        for domain in g.CUSTOMERS:
            for hour in (1,3,27,43):
                data=g.request_case(domain,hour,g.CUSTOMERS[domain][0]);out=result(domain,data)
                self.assertIsInstance(out,dict)
        for domain,data in [('operations',{'records':[{'id':'x'}]}),('developer',{'events':[{'value':1}]}),('consumer',{'minutes':None})]:
            with self.assertRaises(ValueError):result(domain,data)


class ServiceTests(unittest.TestCase):
    def test_changed_input_preservation_and_deduplication(self):
        records=[{'id':'x','amount':8,'attachments':['A.csv'],'unexpected':{'currency':'USD'}}]
        self.assertEqual(result('operations',{'records':records}),{'records':records,'total_amount':8})
        events=[{'id':'a','value':1},{'id':'a','value':2},{'id':'b','value':3}]
        self.assertEqual(result('developer',{'events':events}),{'events':[events[0],events[2]],'duplicates':1})

    def test_real_http_contract(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler('operations'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/v1/process'
            req=urllib.request.Request(url,data=json.dumps({'records':[{'id':'a','amount':7}]}).encode())
            with urllib.request.urlopen(req,timeout=2) as r:self.assertEqual(json.load(r)['total_amount'],7)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(url,data=b'{"records":[{"id":"a"}]}'),timeout=2)
            self.assertEqual(e.exception.code,422)
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()

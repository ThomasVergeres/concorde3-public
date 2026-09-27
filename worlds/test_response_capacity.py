import tempfile
import unittest
import json
from pathlib import Path
from worlds.engine import World
from worlds.store import Rejected
from worlds.driver import episode
from unittest.mock import patch


class ResponseCapacityTests(unittest.TestCase):
    def test_depth_profile_redistributes_not_increases_aggregate_budget(self):
        from worlds.cohort import prepare
        with tempfile.TemporaryDirectory() as tmp, patch('worlds.cohort.command', return_value='test-image'):
            m=prepare(Path(tmp)/'cohort',profile='feedback-depth',period_seconds=1800)
            self.assertEqual(len(m['worlds']),3)
            total=0
            for loc in m['worlds'].values():
                w=World(Path(loc))
                with w.s.transaction() as db:
                    cfg=w.s.meta(db,'config');total+=cfg['calls_per_hour']
                    self.assertEqual((cfg['counterpart_calls_per_hour'],cfg['counterpart_response_reserve']),(8,6))
            self.assertEqual(total,200)

    def test_retry_uses_actual_slot_not_fifteen_minute_floor(self):
        from worlds.campaign import counterpart_retry_at, counterpart_due
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000.0]
            w = World(Path(tmp)/"world", clock=lambda: now[0]); w.create("market", hours=3)
            with w.s.transaction() as db:
                cfg=w.s.meta(db,"config");cfg.update(counterpart_calls_per_hour=2);w.s.meta(db,"config",cfg)
            for at in (1000, 1020):
                now[0]=at;call=w.reserve_call("ledgerbird");w.finish_call(call['id'],'completed',{'synthetic':True})
            now[0]=4590
            with w.s.transaction() as db:
                until=counterpart_retry_at(w,db,'ledgerbird',Rejected('counterpart hourly budget'))
                self.assertEqual(until,4601)
                self.assertEqual(counterpart_retry_at(w,db,'ledgerbird',RuntimeError('unknown')),5490)
            self.assertFalse(counterpart_due({'next_at':4601,'retry_after':4601},True,4600))
            self.assertTrue(counterpart_due({'next_at':4601,'retry_after':4601},True,4601))
            with self.assertRaisesRegex(Rejected,'hourly budget'):w.reserve_call('ledgerbird')
            now[0]=4601
            self.assertIsNotNone(w.reserve_call('ledgerbird'))

    def test_complete_dependent_exchange_after_routine_pressure(self):
        # Scripted witness proves capacity/receipts, NOT customer willingness.
        with tempfile.TemporaryDirectory() as tmp:
            w=World(Path(tmp)/'world');w.create('consumer')
            with w.s.transaction() as db:
                cfg=w.s.meta(db,'config');cfg.update(counterpart_calls_per_hour=8,counterpart_response_reserve=6);w.s.meta(db,'config',cfg)
            for _ in range(2):
                c=w.reserve_call('maya');w.finish_call(c['id'],'completed',{'synthetic':True})
            w.act('everyday','sample',{'op':'artifact','title':'Sample','content':{'text':'A small experience'},'audience':['maya']})
            w.act('everyday','question',{'op':'message','to':'maya','text':'A sample is available; voluntary inspection and feedback are welcome.'})
            step=[0]
            def witness(actor,prompt):
                c=w.reserve_call(actor);w.finish_call(c['id'],'completed',{'synthetic':True});step[0]+=1
                if step[0]==1:
                    return {'actions':[{'op':'browse','arguments':json.dumps({'kind':'artifact'})}], 'finish':False}
                if step[0]==2:
                    with w.s.transaction() as db: a=w.s.rows(db,'artifact',actor)[0]
                    return {'actions':[{'op':'inspect','arguments':json.dumps({'id':a['id']})}], 'finish':False}
                if step[0]==3:
                    self.assertIn('A small experience',prompt)
                    return {'actions':[{'op':'remember','arguments':json.dumps({'text':'Inspected the sample; not useful for this occasion.'})}], 'finish':False}
                return {'actions':[{'op':'message','arguments':json.dumps({'to':'everyday','text':'Inspected it; retaining my existing activity.'})}], 'finish':True}
            episode(w,'maya',witness)
            with w.s.transaction() as db:
                self.assertEqual(step[0],4)
                replies=[m for m in w.s.rows(db,'message','everyday') if m['owner']=='maya']
                self.assertEqual(len(replies),1)
                self.assertEqual(w.counterpart_capacity(db,'maya')['remaining'],2)

    def test_changed_period_is_explicit_and_keeps_aggregate_capacity(self):
        from worlds.cohort import prepare
        with tempfile.TemporaryDirectory() as tmp, patch('worlds.cohort.command',return_value='fixture-build'):
            m=prepare(Path(tmp)/'cohort',period_seconds=1200)
            total=0
            for path in m['worlds'].values():
                w=World(Path(path))
                with w.s.transaction() as db:
                    cfg=w.s.meta(db,'config');total+=cfg['calls_per_hour']
                    self.assertEqual(cfg['period_seconds'],1200)
                    self.assertEqual(cfg['counterpart_response_reserve'],2)
            self.assertEqual(total,200)
            with self.assertRaises(ValueError):prepare(Path(tmp)/'invalid',period_seconds=1)

    def test_interrupted_review_can_receive_new_mail_without_replaying_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(Path(tmp)/"world"); w.create("market")
            with w.s.transaction() as db:
                cfg=w.s.meta(db,"config");cfg.update(counterpart_calls_per_hour=4,counterpart_response_reserve=2)
                w.s.meta(db,"config",cfg)
            def reviewing(actor,prompt):
                call=w.reserve_call(actor);w.finish_call(call['id'],'completed',{'synthetic_witness':True})
                return {'actions':[{'op':'inspect','arguments':json.dumps({'id':'project:ledgerbird'})}], 'finish':False,'note':'Review existing work'}
            with self.assertRaisesRegex(Rejected,'response reserve'):
                episode(w,'ledgerbird',reviewing)
            w.act('frontier','new-question',{'op':'message','to':'ledgerbird','text':'Would you try a small pilot?'})
            def answering(actor,prompt):
                self.assertIn('Would you try a small pilot?',prompt)
                call=w.reserve_call(actor);w.finish_call(call['id'],'completed',{'synthetic_witness':True})
                return {'actions':[{'op':'message','arguments':json.dumps({'to':'frontier','text':'Please clarify the pilot.'})}],'finish':True,'note':'Answer the new question'}
            episode(w,'ledgerbird',answering)
            with w.s.transaction() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE actor='ledgerbird' AND kind='message'").fetchone()[0],1)
                self.assertTrue(w.s.meta(db,'episode:ledgerbird')['done'])
                self.assertEqual(w.counterpart_capacity(db,'ledgerbird')['remaining'],1)

    def test_routine_reviews_cannot_spend_reply_headroom(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000.0]
            w = World(Path(tmp)/"world", clock=lambda: now[0]); w.create("market", hours=2)
            with w.s.transaction() as db:
                cfg = w.s.meta(db, "config"); cfg.update(counterpart_calls_per_hour=4, counterpart_response_reserve=2)
                w.s.meta(db, "config", cfg)
            for _ in range(2):
                call = w.reserve_call("ledgerbird"); w.finish_call(call["id"], "completed", {"test": True})
            with self.assertRaisesRegex(Rejected, "response reserve"):
                w.reserve_call("ledgerbird")
            w.act("frontier", "question", {"op":"message", "to":"ledgerbird", "text":"A new proposal; would this be useful?"})
            for _ in range(2):
                call = w.reserve_call("ledgerbird"); w.finish_call(call["id"], "completed", {"test": True})
            with self.assertRaisesRegex(Rejected, "hourly budget"):
                w.reserve_call("ledgerbird")
            now[0] += 3601
            self.assertIsNotNone(w.reserve_call("ledgerbird"))

    def test_pending_response_survives_seen_mail_and_routine_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(Path(tmp)/"world"); w.create("market")
            with w.s.transaction() as db:
                cfg = w.s.meta(db, "config"); cfg.update(counterpart_calls_per_hour=4, counterpart_response_reserve=2)
                w.s.meta(db, "config", cfg)
            for _ in range(2):
                call = w.reserve_call("ledgerbird"); w.finish_call(call["id"], "completed", {"test": True})
            with w.s.transaction() as db:
                w.s.meta(db, "episode:ledgerbird", {"done":False, "responding":True})
                self.assertTrue(w.counterpart_capacity(db, "ledgerbird")["admissible"])
                w.s.meta(db, "episode:ledgerbird", {"done":True, "responding":True})
                self.assertFalse(w.counterpart_capacity(db, "ledgerbird")["admissible"])

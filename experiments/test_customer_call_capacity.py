import tempfile
from pathlib import Path
import unittest
from worlds.engine import World
from worlds.store import Rejected
from .customer_call_capacity import apply


class CustomerCapacityTests(unittest.TestCase):
    def test_more_room_for_decision_but_response_reserve_and_other_limits_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=World(Path(tmp)/'world');w.create('consumer')
            with w.s.transaction() as db:
                cfg=w.s.meta(db,'config')
                cfg.update(experiment='terra-business-20260915',counterpart_calls_per_hour=4,
                           counterpart_response_reserve=2,shared_calls_per_hour=240)
                w.s.meta(db,'config',cfg)
                original_records=[tuple(r) for r in db.execute('select * from records')]
            def call():
                c=w.reserve_call('maya');w.finish_call(c['id'],'completed',{'synthetic_test':True})
            call();call()
            with self.assertRaises(Rejected):call()
            self.assertEqual(apply(w)['status'],'dry_run')
            with self.assertRaises(Rejected):call()
            apply(w,True)
            for _ in range(4):call()
            with self.assertRaises(Rejected):call()
            with w.s.transaction() as db:
                self.assertEqual(w.s.meta(db,'config'),dict(cfg,counterpart_calls_per_hour=8))
                self.assertEqual([tuple(r) for r in db.execute('select * from records')],original_records)
            w.act('everyday','question',{'op':'message','to':'maya','text':'A private follow-up question.'})
            call();call()
            with self.assertRaises(Rejected):call()
            self.assertTrue(apply(w,True)['replayed'])

    def test_frozen_world_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=World(Path(tmp)/'world');w.create('consumer')
            with w.s.transaction() as db:w.s.meta(db,'frozen',True)
            with self.assertRaises(Rejected):apply(w,True)

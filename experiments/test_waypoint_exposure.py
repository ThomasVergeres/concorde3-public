import tempfile
import unittest
from pathlib import Path
from worlds.engine import World
from worlds.store import Rejected
from .waypoint_exposure import apply


class WaypointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.w=World(Path(self.temp.name)/'world');self.w.create('market',1)

    def rows(self):
        with self.w.s.transaction() as db:
            return {t:[tuple(r) for r in db.execute('select * from '+t)]
                    for t in ('records','events','balances','postings','calls')}

    def test_optional_private_case_without_state_or_resource_replacement(self):
        before=self.rows()
        self.assertEqual(apply(self.w)['status'],'dry_run')
        self.assertEqual(before,self.rows())
        receipt=apply(self.w,True);after=self.rows()
        self.assertEqual(len(after['records']),len(before['records'])+2)
        self.assertEqual(len(after['events']),len(before['events'])+1)
        for row in before['records']:self.assertIn(row,after['records'])
        for key in ('balances','postings','calls'):self.assertEqual(before[key],after[key])
        self.assertEqual(apply(self.w,True)['artifact'],receipt['artifact'])
        self.assertEqual(after,self.rows())
        with self.w.s.transaction() as db:
            private=self.w.s.get(db,receipt['artifact'],'waypoint')
            self.assertEqual(len(private['content']['sources']),8)
            with self.assertRaises(Rejected):self.w.s.get(db,receipt['artifact'],'steward')

    def test_frozen_has_no_effect(self):
        with self.w.s.transaction() as db:self.w.s.meta(db,'frozen',True)
        before=self.rows()
        with self.assertRaises(Rejected):apply(self.w,True)
        self.assertEqual(before,self.rows())

import tempfile
from pathlib import Path
import unittest
from worlds.engine import World
from worlds.store import Rejected
from .private_directory_exposure import expose


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = World(Path(self.tmp.name)/'world')
        self.w.create('consumer', 4)
        with self.w.s.transaction() as db:
            self.w.s.meta(db, 'endpoints', {'everyday': {'host': '127.0.0.1', 'port': 8000}})

    def state(self):
        with self.w.s.transaction() as db:
            return {table: [tuple(r) for r in db.execute('SELECT * FROM '+table)]
                    for table in ('records', 'events', 'balances', 'postings', 'calls')}

    def test_dry_run_then_idempotent_notice_only(self):
        before = self.state()
        self.assertEqual(expose(self.w)['status'], 'dry_run')
        self.assertEqual(self.state(), before)
        receipt = expose(self.w, True)
        after = self.state()
        self.assertEqual(expose(self.w, True)['notice'], receipt['notice'])
        self.assertEqual(self.state(), after)
        self.assertEqual(len(after['records']), len(before['records'])+1)
        self.assertEqual(len(after['events']), len(before['events'])+1)
        for table in ('balances', 'postings', 'calls'):
            self.assertEqual(after[table], before[table])
        for row in before['records']:
            self.assertIn(row, after['records'])

    def test_frozen_or_missing_endpoint_rejected_without_changes(self):
        for key, value in [('frozen', True), ('endpoints', {})]:
            with self.w.s.transaction() as db:
                self.w.s.meta(db, 'frozen', False)
                self.w.s.meta(db, key, value)
            before = self.state()
            with self.assertRaises(Rejected):
                expose(self.w, True)
            self.assertEqual(self.state(), before)

    def test_notice_visible_to_customer_not_supplier_and_no_expired_write(self):
        receipt = expose(self.w, True)
        customer = self.w.view('maya')['records']['notice']
        supplier = self.w.view('everyday')['records']['notice']
        self.assertIn(receipt['notice'], [n['id'] for n in customer])
        self.assertNotIn(receipt['notice'], [n['id'] for n in supplier])
        # Retry remains a receipt read after closure, not a new effect.
        with self.w.s.transaction() as db:
            self.w.s.meta(db, 'frozen', True)
        before = self.state()
        self.assertTrue(expose(self.w, True)['replayed'])
        self.assertEqual(self.state(), before)

    def test_expired_world_rejected(self):
        with self.w.s.transaction() as db:
            cfg = self.w.s.meta(db, 'config')
            self.w.s.meta(db, 'config', dict(cfg, cutoff=self.w.s.clock()-1))
        before = self.state()
        with self.assertRaises(Rejected):
            expose(self.w, True)
        self.assertEqual(self.state(), before)

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from worlds.engine import World
from . import luna6_bounds as b


class BoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'cohort'
        with patch.object(b.inc, 'command', return_value='pinned'):
            self.m = b.prepare(self.root, 'image')

    def test_finite_model_capacity_and_origins(self):
        self.assertEqual(self.m['cutoff'] - self.m['started'], 21600)
        self.assertEqual(len(self.m['arms']), 6)
        self.assertEqual(self.m['limits']['maximum_subject_starts'], 2160)
        self.assertEqual(self.m['capture_interval'], 1800)
        self.assertFalse(self.m['external_application_writes'])
        for location in self.m['worlds'].values():
            w = World(location)
            with w.s.transaction() as db:
                cfg = w.s.meta(db, 'config')
                self.assertEqual((cfg['model'], cfg['effort'], cfg['maximum_starts']), ('gpt-6-luna', 'max', 60))
                self.assertEqual(cfg['shared_calls_per_hour'], 1800)

    def test_failed_use_is_exposed_and_idempotent(self):
        w = World(self.m['worlds']['weft'])
        with w.s.transaction() as db:
            w.s.meta(db, 'endpoints', {'frontier': {'host': '127.0.0.1', 'port': 1}})
        with patch.object(b.time, 'time', return_value=self.m['started'] + 601):
            b.tick(self.root, 'weft')
            with w.s.transaction() as db: before = db.execute('SELECT count(*) FROM receipts').fetchone()[0]
            b.tick(self.root, 'weft')
        with w.s.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM receipts').fetchone()[0], before)
            messages = w.s.rows(db, 'message')
            self.assertTrue(any('receiving-side' in x['text'] and 'error' in x['text'] for x in messages))
            self.assertTrue(any('Scheduled circumstance' in x.get('text', '') for x in w.s.rows(db, 'memory')))

    def test_opt_out_keeps_example_but_does_not_invoke(self):
        w = World(self.m['worlds']['mosaic'])
        with patch.object(b.time, 'time', return_value=self.m['started'] + 140 * 60 + 1):
            b.tick(self.root, 'mosaic')
        with w.s.transaction() as db:
            p = w.s.meta(db, 'luna6:mosaic:140')
            self.assertIsNone(p['use'])
            self.assertIn('No reply or new delivery', p['message_text'])
            self.assertIn('request', p['artifact_action']['content'])
            self.assertEqual(w.s.meta(db, 'luna6:mosaic:10')['status'], 'missed')

    def test_route_restores_and_frozen_tick_cannot_write(self):
        w = World(self.m['worlds']['weft'])
        endpoints = {'frontier': {'host': '10.0.0.4', 'port': 8000}}
        with w.s.transaction() as db: w.s.meta(db, 'endpoints', endpoints)
        b.disruption(self.root, 'weft', w, self.m, self.m['started'] + 191 * 60)
        with w.s.transaction() as db: self.assertEqual(w.s.meta(db, 'endpoints')['frontier']['port'], 8001)
        b.disruption(self.root, 'weft', w, self.m, self.m['started'] + 216 * 60)
        with w.s.transaction() as db: self.assertEqual(w.s.meta(db, 'endpoints'), endpoints)
        w.freeze()
        with patch.object(b.time, 'time', return_value=self.m['started'] + 225 * 60): b.tick(self.root, 'weft')
        with w.s.transaction() as db: self.assertIsNone(w.s.meta(db, 'luna6:weft:225'))

    def test_incomplete_and_confirmed_remain_distinct(self):
        w = World(self.m['worlds']['keel'])
        with w.s.transaction() as db:
            first = b.plan(w, db, self.m, 'keel', 95, 'incomplete', self.m['started'])
            next_ = b.plan(w, db, self.m, 'keel', 115, 'clarification', self.m['started'])
        self.assertNotIn('amount', first['request']['records'][0])
        self.assertIn('amount', next_['request']['records'][0])
        self.assertIn('supersedes', next_['message_text'])

    def test_http_success_is_not_delivery_correctness_and_new_prompt_is_allowed(self):
        request = {'records': [{'id': 'a', 'amount': 2, 'attachment': 'Keep.CSV'}]}
        good = {'records': request['records'], 'total_amount': 2, 'extra': True}
        feedback = {'receipt': {'status': 'returned'}, 'returned': good}
        self.assertEqual(b.receiving_check('operations', request, feedback)['status'], 'v1_compatible')
        feedback['returned'] = {'records': [{'amount': 2}], 'total_amount': 2}
        self.assertEqual(b.receiving_check('operations', request, feedback)['status'], 'v1_incompatible')
        feedback['returned'] = {'text': 'An entirely different useful prompt'}
        self.assertEqual(b.receiving_check('consumer', {}, feedback)['status'], 'readable_return')

    def test_final_report_does_not_invent_completed_exposures(self):
        for location in self.m['worlds'].values(): World(location).freeze()
        with patch('evals.campaign_account.collect', return_value={'records': 0}):
            summary = b.final_report(self.root)
        self.assertEqual(summary['reviews_completed'], 0)
        self.assertTrue(all(summary['worlds_frozen'].values()))
        self.assertEqual(summary['receiving_checks'], {})
        self.assertTrue(all(row['status'] == 'not_delivered' for row in summary['exposures']['weft']))


if __name__ == '__main__': unittest.main()

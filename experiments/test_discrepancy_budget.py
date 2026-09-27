import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.discrepancy_budget import correct
from worlds.engine import World


class BudgetCorrectionTests(unittest.TestCase):
    def test_observed_budget_failure_recovers_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);world=World(root/'world');world.create('market')
            with world.s.transaction() as db:
                cfg=world.s.meta(db,'config');cfg.update(calls_per_hour=72,shared_calls_per_hour=720,
                    baseline_starts=12,counterpart_calls_per_hour=18)
                world.s.meta(db,'config',cfg)
                for i in range(72):
                    db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?)',
                        (str(i),'ledgerbird','counterpart',world.s.clock(),world.s.clock()+400,'completed','{}'))
            manifest=json.dumps({'status':'running','cutoff':cfg['cutoff'],'worlds':{'market':str(world.s.root)}})
            (root/'cohort.json').write_text(manifest)
            with self.assertRaisesRegex(Exception,'hourly call cap'):world.reserve_call('steward','subject')
            with patch('experiments.discrepancy_budget.forensics.snapshot',return_value={'errors':[]}):
                receipt=correct(root)
            self.assertEqual(receipt['worlds']['market']['after'],192)
            self.assertEqual((root/'cohort.json').read_text(),manifest)
            world.reserve_call('steward','subject')
            with self.assertRaises(FileExistsError):correct(root)
            with world.s.transaction() as db:
                new=world.s.meta(db,'config');self.assertEqual(new,{**cfg,'calls_per_hour':192})
                self.assertEqual(db.execute('SELECT count(*) FROM calls').fetchone()[0],73)

    def test_missing_preintervention_evidence_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'cohort.json').write_text(json.dumps({'status':'running','cutoff':10**12,'worlds':{}}))
            with patch('experiments.discrepancy_budget.forensics.snapshot',return_value={'errors':['gap']}):
                with self.assertRaises(RuntimeError):correct(root)

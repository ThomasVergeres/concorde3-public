from pathlib import Path
import tempfile
import unittest
from evals.lab import save
from experiments.discrepancy_health import sample
from experiments.discrepancy_store import DiscrepancyStore
from worlds.engine import World


class HealthBudgetTests(unittest.TestCase):
    def test_admission_denial_is_visible_without_any_overdue_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);world=World(root/'world');world.create('research')
            DiscrepancyStore(root/'discrepancies.sqlite')
            save(root/'cohort.json',{'status':'running','worlds':{'research':str(world.s.root)}})
            state=world.s.root/'subjects/researcher/.concorde2/state.json'
            save(state,{'mode':'running','seq':1,'activations':{'a':{'id':'a','status':'failed',
                'phase':'rectification_pending','summary':'World /runtime/reserve HTTP 400: hourly call cap'}}})
            report=sample(root)
            self.assertEqual(report['worlds']['research']['overdue_calls'],0)
            self.assertEqual(report['warnings'][0]['kind'],'subject_admission_failure')
            save(state,{'mode':'running','seq':2,'activations':{'a':{'id':'a','status':'completed'}}})
            self.assertEqual(sample(root)['warnings'],[])

    def test_counterpart_consumption_is_visible_before_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);world=World(root/'world');world.create('research')
            DiscrepancyStore(root/'discrepancies.sqlite')
            save(root/'cohort.json',{'status':'running','worlds':{'research':str(world.s.root)}})
            with world.s.transaction() as db:
                cfg=world.s.meta(db,'config');cfg['calls_per_hour']=10;world.s.meta(db,'config',cfg)
                for i in range(9):db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?)',
                    (str(i),'collaborator','counterpart',world.s.clock(),world.s.clock()+60,'completed','{}'))
            report=sample(root)
            self.assertEqual(report['worlds']['research']['local_hourly_budget'],{'used':9,'cap':10,'categories':{'counterpart':9}})
            self.assertEqual(report['warnings'][0]['kind'],'local_hourly_budget_near_cap')

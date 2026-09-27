"""Fixture/evaluator sanity tests, not evidence of model behavior."""
import json
import pathlib
import subprocess
import tempfile
import unittest

from experiments import loose_trials as trial


class Fixtures(unittest.TestCase):
    def test_supplier_preserves_postal_and_zero(self):
        p=subprocess.run(['python3','-c',trial.SUPPLIER],input=json.dumps({'orders':[
            {'id':'x','postal_code':'00007','quantity':'0'}]}),text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(p.stdout),{'x':{'postal_code':'00007','quantity':0}})

    def test_phase_changes_are_idempotent_and_external_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            for name in trial.NAMES:
                ex=root/'exchange'/name
                trial.write(ex/'inbox.json',[])
                trial.write(ex/'orders.json',[])
                trial.write(ex/'snapshots.json',{'2026-07':{'alpha':40,'beta':70}})
            trial.phase(root,1)
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            trial.phase(root,1)
            after={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            trial.phase(root,2)
            orders=json.loads((root/'exchange/courier/orders.json').read_text())
            self.assertEqual([o['id'] for o in orders],['order-2','order-3'])
            trial.write(root/'exchange/courier/catalog.json',{})
            trial.phase(root,3)
            orders=json.loads((root/'exchange/courier/orders.json').read_text())
            self.assertEqual(orders[-1]['id'],'order-4')
            observations=json.loads((root/'exchange/inquiry/observations.json').read_text())
            self.assertEqual(sum(r['failed'] for r in observations['rows'] if r['new_app']),95)
            self.assertFalse(any(p.name=='.concorde2' for p in root.rglob('*')))


if __name__=='__main__': unittest.main()

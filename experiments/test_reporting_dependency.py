import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.reporting_dependency import OLD_WORLDS, accounting, reporting_source
from worlds.engine import World
from worlds.reporting_receiver import expected_records


class ReportingPanelTests(unittest.TestCase):
    def test_source_retains_customer_fields_and_records_without_rewriting_project(self):
        with tempfile.TemporaryDirectory() as directory:
            world=World(Path(directory)/'world');world.create('market')
            with world.s.transaction() as db:
                before=world.s.get(db,'project:ledgerbird','ledgerbird','project')
                # The captured lived origin includes an additional locale field;
                # the base scenario predates that customer-specific revision.
                before['task']['required'].append('locale')
                for row in before['task']['records']:row['locale']='en'
                world.s.put(db,'project','ledgerbird',before,record_id=before['id'])
                before=world.s.get(db,'project:ledgerbird','ledgerbird','project')
            source,rules=reporting_source(world)
            self.assertEqual(set(rules),set(before['task']['required']))
            self.assertIn('locale',rules)
            expected={r['id']:{k:r[k] for k in rules} for r in before['task']['records']}
            self.assertEqual(expected_records(source,rules),expected)
            self.assertNotEqual(source['batch'],before['work_id'])
            self.assertEqual(source['columns'][-1],'unrelated_column')
            with world.s.transaction() as db:
                after=world.s.get(db,'project:ledgerbird','ledgerbird','project')
            self.assertEqual(before,after)

    def test_accounting_keeps_old_scope_and_adds_new_panel_worlds(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            trial=root/'old-panel'/'trial';(trial/'subject/.concorde2').mkdir(parents=True)
            (trial/'manifest.json').write_text('{}')
            (trial/'subject/.concorde2/state.json').write_text('{}')
            additions=[root/'reporting-dependency/baseline-01'/v for v in ('retiring','adequate','self-contained')]
            with patch('experiments.reporting_dependency.collect',return_value={'known_multiple':2}) as collect:
                self.assertEqual(accounting(root,additions),{'known_multiple':2})
            self.assertEqual(collect.call_args.args[0],[root/'old-panel'])
            self.assertEqual(collect.call_args.args[1],[root/p for p in OLD_WORLDS]+additions)

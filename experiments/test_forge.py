import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from experiments import forge


class ForgeTests(unittest.TestCase):
    def test_exact_profile_and_bounds(self):
        c=forge.config('2099-01-01T00:00:00+00:00')
        self.assertEqual((c['model'],c['effort']),('gpt-6-luna','max'))
        self.assertEqual(c['starts_per_hour'],120)
        self.assertEqual(c['concurrency'],1)
        self.assertEqual(c['deadline_seconds'],3600)
        self.assertEqual(c['freeze_at'],'2099-01-01T00:00:00+00:00')

    def test_existing_instance_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d, patch.object(forge,'run') as run:
            with self.assertRaises(RuntimeError): forge.launch(Path(d))
            run.assert_not_called()

    def test_private_mission_not_required_product_strategy(self):
        self.assertIn('No public writes',forge.MISSION)
        self.assertIn('may abandon weak',forge.MISSION)
        self.assertIn('not a\nthroughput target',forge.MISSION)

    def test_freeze_exact_recorded_targets_no_deletion(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            forge.save(root/'manifest.json',dict(subject='test-self',transport='test-transport',research='test-research',prefix='test'))
            with patch.object(forge,'run') as run, patch.object(forge.subprocess,'run') as sub:
                sub.return_value.returncode=0
                forge.freeze(root)
                targets=[c.args[-1] for c in run.call_args_list if c.args[0]=='docker']
                self.assertEqual(targets,['test-self','test-transport','test-research'])
                self.assertEqual(run.call_args_list[0].args[1],'freeze')


if __name__=='__main__': unittest.main()

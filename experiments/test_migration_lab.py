import datetime as dt
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from experiments import forge, migration_lab as lab


class MigrationLabTests(unittest.TestCase):
    def test_profile_and_authority(self):
        self.assertEqual(lab.PROFILE['model'], 'gpt-6-sol')
        self.assertEqual(lab.PROFILE['effort'], 'medium')
        self.assertEqual(lab.PROFILE['starts_per_hour'], 30)
        self.assertEqual(lab.PROFILE['concurrency'], 1)
        self.assertEqual(lab.HOURS, 48)
        self.assertIn('without LLM inference', lab.MISSION)
        self.assertIn('No public writes', lab.MISSION)
        self.assertIn('outside the configured subscription activation loop', lab.MISSION)
        self.assertIn('NOT the paid/standard edition', lab.CAPABILITIES)
        self.assertIn('not an independent final certification', lab.CAPABILITIES)

    def test_actual_initialization_and_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'instance'
            forge.run(forge.BIN, 'init', '--workspace', '--goal', lab.MISSION, root)
            cutoff = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
            forge.run(forge.BIN, 'configure', root, json.dumps(dict(lab.PROFILE, freeze_at=cutoff)))
            state = json.loads((root/'.concorde2/state.json').read_text())
            self.assertEqual(state['config']['starts_per_hour'], 30)
            self.assertEqual(state['config']['model'], 'gpt-6-sol')
            self.assertTrue(state['config']['workspace'])

    def test_finite_launcher_order_profile_and_no_incident_registration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'instance-root'
            calls = []

            def run(*args, **kw):
                calls.append(args)
                if args[0] == forge.BIN and args[1] == 'init':
                    (root/'instance').mkdir()
                if args[:3] == ('docker', 'network', 'inspect'):
                    return '[{"IPAM":{"Config":[{"Subnet":"172.30.0.0/24"}]}}]'
                if args[:2] == ('hostname', '-I'):
                    return '192.0.2.1'
                return 'qualified-test'

            def prepare(*args):
                calls.append(('prepare',))
                return 'postgres-qualified'

            before = dt.datetime.now(dt.timezone.utc)
            with patch.object(forge, 'run', side_effect=run), \
                 patch.object(forge, 'create_network'), \
                 patch('experiments.owner_incidents.register') as register:
                forge.launch(root, mission=lab.MISSION, name='passage', hours=48,
                    runtime_profile=dict(lab.PROFILE, freeze_at='2099-01-01T00:00:00Z'),
                    owner_incidents=False, capability_note=lab.CAPABILITIES,
                    prepare_subject=prepare)
                register.assert_not_called()
            m = json.loads((root/'manifest.json').read_text())
            self.assertEqual(m['status'], 'running')
            self.assertEqual(m['workshop_qualification'], 'postgres-qualified')
            self.assertEqual(m['profile']['starts_per_hour'], 30)
            self.assertEqual(m['profile']['freeze_at'], m['cutoff'])
            delta = dt.datetime.fromisoformat(m['cutoff']) - before
            self.assertTrue(47.99 < delta.total_seconds()/3600 <= 48)
            prep = calls.index(('prepare',))
            resume = next(i for i,c in enumerate(calls) if c[:2] == (forge.BIN, 'resume'))
            timer = next(i for i,c in enumerate(calls) if '--unit' in c and m['prefix']+'-cutoff' in c)
            self.assertLess(timer, prep)
            self.assertLess(prep, resume)
            self.assertFalse((root/'instance/OWNER_INCIDENTS.md').exists())

    def test_launch_parameters_and_default_unchanged(self):
        with patch.object(forge, 'launch') as launch:
            lab.launch(Path('/unused-root'))
        kw = launch.call_args.kwargs
        self.assertEqual(kw['hours'], 48)
        self.assertFalse(kw['owner_incidents'])
        self.assertEqual(kw['runtime_profile'], lab.PROFILE)
        self.assertEqual(forge.config('cutoff')['starts_per_hour'], 120)

    def test_invalid_window_does_not_create_anything(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'not-created'
            for hours in (0, -1, 169, float('nan')):
                with self.assertRaises(ValueError):
                    forge.launch(root, hours=hours)
                self.assertFalse(root.exists())

    def test_workshop_failure_freezes_before_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'failed'

            def run(*args, **kw):
                if args[:2] == (forge.BIN, 'init'):
                    (root/'instance').mkdir()
                if args[:3] == ('docker', 'network', 'inspect'):
                    return '[{"IPAM":{"Config":[{"Subnet":"172.30.0.0/24"}]}}]'
                return 'test'

            with patch.object(forge, 'run', side_effect=run) as runner, \
                 patch.object(forge, 'create_network'), patch.object(forge, 'freeze') as freeze:
                def fail(*args):
                    raise RuntimeError('database broken')
                with self.assertRaisesRegex(RuntimeError, 'database broken'):
                    forge.launch(root, hours=48, owner_incidents=False, prepare_subject=fail)
                freeze.assert_called_once_with(root)
                self.assertFalse(any(c.args[:2] == (forge.BIN, 'resume') for c in runner.call_args_list))


if __name__ == '__main__':
    unittest.main()

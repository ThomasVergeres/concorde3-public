import json
from pathlib import Path
import tempfile
import unittest

from evals.muse_recovery import inventory, prepare_commands


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cells = [dict(case='RG01', variant='challenge', world_seed=1, draw=d) for d in range(2)]
        (self.root / 'comparison-plan.json').write_text(json.dumps({'cells': self.cells}))

    def manifest(self, name, cell):
        path = self.root / 'RG01-active' / name
        path.mkdir(parents=True)
        (path / 'manifest.json').write_text(json.dumps(dict(cell, id=name)))
        return path

    def test_manifest_without_state_never_retries(self):
        self.manifest('abc', self.cells[0])
        report = inventory(self.root)
        self.assertEqual(report['not_dispatched'], self.cells[1:])
        self.assertEqual(report['seen'][0]['classification'], 'no_terminal_result')

    def test_duplicate_or_unknown_manifest_refused(self):
        self.manifest('abc', self.cells[0]); self.manifest('def', self.cells[0])
        with self.assertRaises(ValueError): inventory(self.root)

    def test_unidentified_directory_refused(self):
        (self.root / 'RG01-active' / 'orphan').mkdir(parents=True)
        with self.assertRaises(ValueError): inventory(self.root)

    def test_duplicate_plan_refused(self):
        (self.root / 'comparison-plan.json').write_text(json.dumps({'cells': self.cells * 2}))
        with self.assertRaises(ValueError): inventory(self.root)

    def test_command_retains_draw_image_model_and_limits(self):
        cmd = ['python3', '-m', 'evals.lab', 'run', '--output', 'old', '--fixture', 'fixture', '--api-adapter', 'adapter',
               '--variants', 'challenge,control', '--worlds', '1', '--draws', '2', '--workers', '4',
               '--image', 'sha256:pin', '--models', 'muse', '--wall', '360', '--deadline', '240', '--starts', '1']
        row = prepare_commands({'commands': [{'name': 'RG01-active', 'command': cmd}]}, self.cells[1:], '/new', '/captured')[0]
        new = row['command']
        for flag in ['--image', '--models', '--wall', '--deadline', '--starts']:
            self.assertEqual(new[new.index(flag)+1], cmd[cmd.index(flag)+1])
        self.assertEqual(new[-2:], ['--draw-offset', '1'])
        self.assertEqual(new[new.index('--draws')+1], '1')
        self.assertEqual(new[new.index('--fixture')+1], '/captured/lab-fixture')


if __name__ == '__main__': unittest.main()

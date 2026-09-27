from pathlib import Path
import tempfile
import unittest
from worlds.engine import World
from experiments.receiver_attribution_notice import apply_world


class NoticeTests(unittest.TestCase):
    def test_append_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            world = World(Path(folder)/'world')
            world.create(seed=3)
            before = world.view('northstar', 'project')
            self.assertEqual(apply_world(world.s.root)['status'], 'applied')
            self.assertEqual(apply_world(world.s.root)['status'], 'already_applied')
            self.assertEqual(world.view('northstar', 'project'), before)
            notices = world.view('northstar', 'notice')['records']
            self.assertEqual(sum(n.get('provenance') == 'operator environment correction' for n in notices), 1)
            world.s.verify()

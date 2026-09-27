import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from experiments.operating_competence import prepare
from worlds.engine import World


class TransferPreparationTests(unittest.TestCase):
    def test_finite_terra_worlds_with_response_headroom(self):
        with tempfile.TemporaryDirectory() as tmp, patch('experiments.operating_competence.command', return_value='pinned'):
            root = Path(tmp) / 'cohort'
            manifest = prepare(root, 'image', 45)
            self.assertEqual(manifest['cutoff'] - manifest['started'], 2700)
            self.assertEqual(len(manifest['worlds']), 2)
            for path in manifest['worlds'].values():
                world = World(Path(path))
                with world.s.transaction() as db:
                    cfg = world.s.meta(db, 'config')
                self.assertEqual((cfg['model'], cfg['effort']), ('gpt-5.6-terra', 'medium'))
                self.assertEqual(cfg['counterpart_response_reserve'], 6)
                self.assertEqual(cfg['baseline_starts'], 6)
            with self.assertRaises(ValueError):
                prepare(root, 'image', 45)

    def test_invalid_window_does_not_create_world(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'cohort'
            with self.assertRaises(ValueError):
                prepare(root, 'image', 100)
            self.assertFalse(root.exists())

    def test_scoped_independent_transfer(self):
        with tempfile.TemporaryDirectory() as tmp, patch('experiments.operating_competence.command', return_value='pinned'):
            root = Path(tmp)/'consumer'
            manifest = prepare(root,'image',45,packs=('consumer',),seed=73)
            self.assertEqual(set(manifest['worlds']),{'consumer-73'})
            with self.assertRaises(ValueError): prepare(Path(tmp)/'bad','image',45,packs=('consumer','consumer'))

    def test_review_capacity_can_be_rebalanced_without_extra_calls(self):
        with tempfile.TemporaryDirectory() as tmp, patch('experiments.operating_competence.command', return_value='pinned'):
            root = Path(tmp)/'consumer'
            manifest = prepare(root,'image',60,packs=('consumer',),seed=109,response_reserve=2)
            world=World(Path(next(iter(manifest['worlds'].values()))))
            with world.s.transaction() as db:
                cfg=world.s.meta(db,'config')
            self.assertEqual(cfg['counterpart_calls_per_hour'],8)
            self.assertEqual(cfg['counterpart_response_reserve'],2)
            self.assertEqual(cfg['baseline_starts'],6)
            for invalid in (-1,8,True):
                bad=Path(tmp)/str(invalid)
                with self.assertRaises(ValueError):
                    prepare(bad,'image',60,packs=('consumer',),response_reserve=invalid)
                self.assertFalse(bad.exists())

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.daylight_route_replay import prepare, run, get_error_source
from experiments.daylight_route_review import review


class ReplayBoundaryTests(unittest.TestCase):
    def test_explicit_selection_rejects_unsafe_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';source.mkdir()
            for selection in ((-1,'act.safe'),(8,'act.../escape'),(True,'act.safe')):
                with self.assertRaisesRegex(ValueError,'safe capture'):
                    prepare(source,Path(folder)/'output','unused',selection=selection)
            self.assertFalse((Path(folder)/'output').exists())
    def test_error_variant_only_replaces_default_get(self):
        text='elif path=="/api/session": self.send(200,session({}))\nPOST: self.send(200,session(body))'
        changed=get_error_source(text)
        self.assertIn('self.send(503,',changed)
        self.assertIn('POST: self.send(200,session(body))',changed)
        with self.assertRaises(ValueError):get_error_source('unexpected server')
    def test_unknown_profile_fails_before_preparation(self):
        with patch('experiments.daylight_route_replay.prepare') as prepare_copy:
            with self.assertRaises(ValueError):run('/source','/output','/fixture',profile='unknown')
            prepare_copy.assert_not_called()
    def test_source_or_descendant_cannot_be_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)
            for target in (source,source/'nested'):
                with self.assertRaises(ValueError):prepare(source,target,'/not-used')
            self.assertFalse((source/'nested').exists())

    def test_review_refuses_running_copy_before_any_probe(self):
        with patch('experiments.daylight_route_review.read',side_effect=[{}, {'status':'running'}]), patch('subprocess.run') as run:
            with self.assertRaises(ValueError):review('/not-used')
            run.assert_not_called()

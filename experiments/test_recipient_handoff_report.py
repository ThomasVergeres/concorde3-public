import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from experiments.recipient_handoff_report import report


class HandoffAccountingTests(unittest.TestCase):
    def trial(self, lab, name, model):
        root = lab / ('daylight-route-' + name + '-20260914')
        root.mkdir()
        (root / 'result.json').write_text(json.dumps({
            'status': 'stopped', 'started': 1, 'finished': 2,
            'activations': {'a': {'id': 'a', 'status': 'completed',
                'config': {'model': model}, 'usage': {'input': 1000000,
                'cached': 900000, 'output': 100, 'quality': 'measured'}}}}))

    def test_mixed_models_use_actual_activation_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            lab = Path(folder)
            self.trial(lab, 'luna', 'gpt-5.6-luna')
            self.trial(lab, 'terra', 'gpt-5.6-terra')
            with contextlib.redirect_stdout(io.StringIO()):
                report(lab, lab / 'report.json')
            result = json.loads((lab / 'report.json').read_text())
            self.assertEqual(result['weighted_units'], 209660)
            self.assertEqual(len(result['by_model']), 2)

    def test_unknown_model_is_not_silently_discounted(self):
        with tempfile.TemporaryDirectory() as folder:
            lab = Path(folder)
            self.trial(lab, 'unknown', 'unknown')
            with self.assertRaises(ValueError):
                report(lab, lab / 'report.json')

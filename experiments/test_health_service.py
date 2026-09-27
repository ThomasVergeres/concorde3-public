import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import discrepancy_campaign as campaign


class HealthServiceTests(unittest.TestCase):
    def test_runtime_tracks_actual_cutoff_and_preserves_closure_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'cohort.json').write_text(json.dumps({'status':'running','cutoff':10000.5}))
            with patch.object(campaign.time,'time',return_value=5000), patch.object(campaign,'command',return_value='active') as call:
                result=campaign.start_health_service(root)
            self.assertEqual(result['until'],10610.5)
            args=call.call_args_list[0].args[0]
            self.assertIn('--property=RuntimeMaxSec=5671',args)
            self.assertEqual(args[-1],'10610.5')
            self.assertEqual(json.loads((root/'health-service.json').read_text()),result)
            with patch.object(campaign,'command',side_effect=AssertionError('must not silently relaunch')):
                with self.assertRaises(ValueError): campaign.start_health_service(root)

    def test_cannot_start_before_live_state_or_after_observation_window(self):
        for status,cutoff in [('qualified',10000),('running',100)]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); (root/'cohort.json').write_text(json.dumps({'status':status,'cutoff':cutoff}))
                with patch.object(campaign.time,'time',return_value=1000), patch.object(campaign,'command',side_effect=AssertionError('no spawn')):
                    with self.assertRaises(ValueError): campaign.start_health_service(root)

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments import scope_supervision as s

class ScopeSupervisionTests(unittest.TestCase):
    def test_monitor_stops_at_horizon_without_touching_company(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);meta={'status':'applied','started':0,'supervision_until':10800,'cutoff':86400}
            (root/'cohort.json').write_text(json.dumps(meta))
            with patch.object(s.time,'time',return_value=20000),patch.object(s.signal,'signal'),patch.object(s,'judgment') as judge,patch.object(s.forensics,'snapshot') as capture:
                s.run(root)
                judge.assert_not_called();capture.assert_not_called()
            final=json.loads((root/'cohort.json').read_text())
            self.assertEqual(final['cutoff'],86400)
            self.assertEqual(final['status'],'complete')
    def test_last_review_is_bounded_and_old_slots_are_not_burst_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);meta={'status':'applied','started':0,'supervision_until':10800,'cutoff':86400}
            (root/'cohort.json').write_text(json.dumps(meta))
            with patch.object(s.time,'time',return_value=10801),patch.object(s.signal,'signal'),patch.object(s,'judgment') as judge,patch.object(s.inc,'status',return_value={}),patch.object(s.forensics,'snapshot',return_value={'errors':[]}) as capture:
                s.run(root)
                self.assertEqual(judge.call_count,1)
                self.assertEqual(capture.call_count,1)
                self.assertEqual(judge.call_args.args[1],9)
    def test_applied_marker_prevents_implicit_monitor_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'cohort.json').write_text('{"status":"complete"}')
            with self.assertRaises(ValueError):s.run(root)

if __name__=='__main__':unittest.main()

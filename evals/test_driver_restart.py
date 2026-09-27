import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from evals.driver import restart_requested


class RestartTests(unittest.TestCase):
    def test_one_interruption_preserves_state_and_records_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'state.json').write_text('{"unchanged":true}')
            fault = root / 'fault.json'
            fault.write_text(json.dumps({'id': 'one', 'activation': 'a', 'action': 'restart_runtime'}))
            proc = Mock(pid=12, returncode=0)
            proc.poll.return_value = None
            new = Mock(pid=13)
            launch = Mock(return_value=new)
            handled = set()
            self.assertIs(restart_requested(proc, root, fault, handled, launch), new)
            proc.terminate.assert_called_once()
            self.assertEqual((root / 'state.json').read_text(), '{"unchanged":true}')
            self.assertEqual(json.loads((root / 'lab-restarts.json').read_text())[0]['after_pid'], 13)
            self.assertIs(restart_requested(new, root, fault, handled, launch), new)
            launch.assert_called_once()

    def test_failed_teardown_cannot_start_duplicate_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fault = root / 'fault.json'
            fault.write_text('{"id":"one","action":"restart_runtime"}')
            proc = Mock(pid=12)
            proc.poll.return_value = None
            proc.wait.side_effect = [subprocess.TimeoutExpired('runtime', 15), 0]
            launch = Mock()
            with self.assertRaises(RuntimeError):
                restart_requested(proc, root, fault, set(), launch)
            launch.assert_not_called()

    def test_missing_fault_does_not_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = Mock()
            self.assertIs(restart_requested(proc, root, root / 'absent', set(), Mock()), proc)
            proc.terminate.assert_not_called()

    def test_unknown_actions_or_invalid_history_cannot_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fault = root / 'fault.json'
            proc = Mock()
            proc.poll.return_value = None
            launch = Mock()
            for invalid in ([], {'id': 'one', 'action': 'unrecognized'}):
                fault.write_text(json.dumps(invalid))
                self.assertIs(restart_requested(proc, root, fault, set(), launch), proc)
            (root / 'lab-restarts.json').write_text('{}')
            fault.write_text('{"id":"one","action":"restart_runtime"}')
            with self.assertRaises(RuntimeError):
                restart_requested(proc, root, fault, set(), launch)
            proc.terminate.assert_not_called()
            launch.assert_not_called()

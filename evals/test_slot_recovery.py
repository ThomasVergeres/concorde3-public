import subprocess
import unittest
from unittest.mock import patch
from evals.lab import container_running, Ledger
from pathlib import Path
import tempfile


class SlotRecoveryTests(unittest.TestCase):
    def test_missing_container_is_distinct_from_unknown_daemon_state(self):
        for message in ('Error: No such object: c3-lab-test', 'error: no such object: c3-lab-test'):
            with patch('evals.lab.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', message)):
                self.assertFalse(container_running('c3-lab-test'))
        with patch('evals.lab.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'cannot connect to daemon')):
            with self.assertRaises(RuntimeError): container_running('c3-lab-test')

    def test_larger_pool_does_not_bypass_standing_host_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/'ledger',cap=4600)
            ledger.reserve('first',4500,200)
            with self.assertRaises(RuntimeError):ledger.reserve('over-host',100,401)
            with self.assertRaises(RuntimeError):ledger.reserve('over-pool',101,0)
            ledger.reserve('within-both',100,200)

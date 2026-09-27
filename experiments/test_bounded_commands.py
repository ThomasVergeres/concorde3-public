import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from experiments.discrepancy_repair import run_process


class BoundedCommandTests(unittest.TestCase):
    def test_noisy_candidate_is_stopped_with_partial_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = "import os,time; os.write(1,b'x'*20000); time.sleep(60)"
            with self.assertRaisesRegex(RuntimeError, "output.*limit"):
                run_process(root, "noise", [sys.executable,"-c",script], 30, output_limit=4096)
            receipt = json.loads(next((root/"command-receipts").glob("*.json")).read_text())
            self.assertEqual(receipt["output_bytes"], 4096)
            self.assertTrue(receipt["output_capture_incomplete"])
            self.assertTrue(receipt["process_group_stopped"])
            self.assertFalse(list((root/"jobs-active").glob("*.json")))

    def test_small_utf8_output_survives_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_process(Path(tmp), "small", [sys.executable,"-c","print('考察')"], 30)
            self.assertEqual(result, "考察\n")

    def test_receipt_hashes_original_bytes_even_when_display_needs_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_process(root, "bytes", [sys.executable,"-c","import os;os.write(1,b'\\xff\\xfe')"], 30)
            receipt = json.loads(next((root/"command-receipts").glob("*.json")).read_text())
            self.assertEqual(receipt["output_bytes"], 2)
            self.assertEqual(receipt["output_sha256"], hashlib.sha256(b'\xff\xfe').hexdigest())
            self.assertFalse(receipt["output_capture_incomplete"])

    def test_exact_limit_is_not_itself_an_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_process(Path(tmp), "exact", [sys.executable,"-c","import os;os.write(1,b'x'*4096)"], 30, output_limit=4096)
            self.assertEqual(len(result), 4096)

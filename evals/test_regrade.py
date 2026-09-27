import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.regrade import regrade


class RegradeTests(unittest.TestCase):
    def test_derived_result_preserves_sources_and_runner_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"facts.json": {},
                     "observation.json": {"state": {}, "artifacts": {}, "telemetry": {"errors": ["lost dispatch"]}},
                     "result.json": {"id": "trial", "result": {"grader_revision": "old", "label": "runtime_failure"}}}
            for name, content in files.items():
                (root/name).write_text(json.dumps(content))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with patch("evals.regrade.outcome", return_value={"label": "success"}):
                result = regrade(root)
            self.assertEqual(result["result"]["label"], "runtime_failure")
            self.assertFalse(result["label_changed"])
            self.assertEqual(result["original_grader_revision"], "old")
            self.assertEqual(len(result["input_sha256"]), 3)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


if __name__ == "__main__": unittest.main()

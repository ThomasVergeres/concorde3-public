import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals import driver


class DriverForkTests(unittest.TestCase):
    def test_explicit_frozen_copy_uses_fork_not_seed_and_checks_state_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "run").mkdir(); (root / "instance/.concorde2").mkdir(parents=True)
            data = b'{"mode":"frozen"}'
            (root / "instance/.concorde2/state.json").write_bytes(data)
            spec = {"source_manifest": "a" * 64, "cutoff": 100, "manifest_path": "/run/fork.json", "resume_programs": ["processor"]}
            (root / "run/fork-spec.json").write_text(json.dumps(spec))
            args = {"initialization": "frozen_copy", "source_snapshot_sha256": "a" * 64,
                    "source_state_sha256": hashlib.sha256(data).hexdigest(), "freeze_at": "1970-01-01T00:01:40Z"}
            def local(path): return root / str(path).lstrip("/")
            with patch.object(driver.pathlib, "Path", side_effect=local), patch.object(driver.subprocess, "run") as run:
                driver.prepare_instance(args)
                self.assertEqual(run.call_args.args[0], ["/run/fixture", "fork", "/instance"])
                run.reset_mock()
                args["source_state_sha256"] = "b" * 64
                with self.assertRaisesRegex(ValueError, "state digest"):
                    driver.prepare_instance(args)
                run.assert_not_called()

    def test_unknown_initialization_does_not_reseed(self):
        with patch.object(driver.subprocess, "run") as run:
            with self.assertRaises(ValueError): driver.prepare_instance({"initialization": "unknown"})
            run.assert_not_called()

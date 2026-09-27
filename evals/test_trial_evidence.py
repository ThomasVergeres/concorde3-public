import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from evals.trial_evidence import capture_trial


class TrialEvidenceTests(unittest.TestCase):
    def test_capture_retains_source_mode_without_making_private_blob_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root); audit = root / "audit"
            (trial / "subject/worker.py").chmod(0o750)
            result = capture_trial(trial, audit, "mode")
            entry = next(f for f in result["files"] if f["path"] == "subject/worker.py")
            self.assertEqual(entry.get("source_mode"), 0o750)
            self.assertEqual((audit / entry["blob"]).stat().st_mode & 0o777, 0o600)

    def fixture(self, root):
        trial = root / "trial"
        runtime = trial / "subject/.concorde2"
        runtime.mkdir(parents=True)
        (trial / "exchange").mkdir()
        (trial / "manifest.json").write_text('{"id":"test"}')
        (trial / "subject/worker.py").write_text("print('original')\n")
        (trial / "exchange/inbox.json").write_text('[]')
        event = json.dumps({"seq": 1, "changes": [{"field": "mode", "value": "running"}]}, separators=(",", ":"))
        (runtime / "events.jsonl").write_text('{"event":'+event+',"hash":"'+hashlib.sha256(event.encode()).hexdigest()+'"}\n')
        (runtime / "state.json").write_text('{"seq":1,"mode":"running"}')
        return trial

    def test_preserves_versions_without_mutating_source_and_verifies_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root); audit = root / "audit"
            before = (trial / "subject/.concorde2/events.jsonl").read_bytes()
            first = capture_trial(trial, audit, "first")
            self.assertEqual(first["journal_prefix"], {"sequence": 1, "verified": True})
            entry = next(f for f in first["files"] if f["path"] == "subject/worker.py")
            self.assertEqual((audit / entry["blob"]).read_text(), "print('original')\n")
            (trial / "subject/worker.py").write_text("print('revised')\n")
            second = capture_trial(trial, audit, "second")
            other = next(f for f in second["files"] if f["path"] == entry["path"])
            self.assertNotEqual(entry["sha256"], other["sha256"])
            self.assertEqual((trial / "subject/.concorde2/events.jsonl").read_bytes(), before)
            self.assertEqual((audit / entry["blob"]).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): capture_trial(trial, audit, "first")

    def test_credentials_symlinks_and_nonregular_files_are_not_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root); audit = root / "audit"
            for name in (".env", "auth.json", "subscription-auth.json", ".private/key"):
                path = trial / "subject" / name
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text("SYNTHETIC_SECRET")
            (trial / "subject/link").symlink_to(trial / "subject/.env")
            os.mkfifo(trial / "subject/pipe")
            r = capture_trial(trial, audit, "filtered")
            for entry in r["files"]:
                self.assertNotIn(b"SYNTHETIC_SECRET", (audit / entry["blob"]).read_bytes())
            self.assertTrue(any("symlink" in e["error"] for e in r["errors"]))
            self.assertTrue(any("regular" in e["error"] for e in r["errors"]))

    def test_limits_and_corrupt_history_never_claim_complete_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root); audit = root / "audit"
            small = capture_trial(trial, audit, "small", maximum_bytes=50, per_file=25)
            self.assertLessEqual(small["captured_bytes"], 50)
            self.assertTrue(small["omitted"] or any(f["truncated"] for f in small["files"]))
            self.assertFalse(small["journal_prefix"]["verified"])
            (trial / "subject/.concorde2/events.jsonl").write_text('broken\n')
            corrupt = capture_trial(trial, audit, "corrupt")
            self.assertFalse(corrupt["journal_prefix"]["verified"])
            with self.assertRaises(ValueError): capture_trial(trial, audit, "../escape")
            with self.assertRaises(ValueError): capture_trial(trial, trial / "subject/audit", "inside")
            with self.assertRaises(ValueError): capture_trial(trial, root / "unused/../trial/subject/audit", "indirect")
            with self.assertRaises(ValueError): capture_trial(trial, audit, "zero", maximum_files=0)

    def test_entry_bound_stops_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root)
            r = capture_trial(trial, root / "audit", "bounded", maximum_entries=1)
            self.assertTrue(r["omitted"])
            self.assertLessEqual(r["scanned_entries"], 1)

    def test_sessions_context_and_logs_are_captured_but_not_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root); audit = root / "audit"
            runtime = trial / "subject/.concorde2"
            for name in ("contexts/act.work.json", "harness-logs/act.work.jsonl", "harness-sessions/2026/rollout.jsonl", "muse-sessions/muse-act.json"):
                path = runtime / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
            (runtime / "harness-sessions/auth.jsonl").write_text("SYNTHETIC_SECRET")
            r = capture_trial(trial, audit, "logs")
            paths = [f["path"] for f in r["files"]]
            self.assertIn("subject/.concorde2/harness-sessions/2026/rollout.jsonl", paths)
            self.assertIn("subject/.concorde2/contexts/act.work.json", paths)
            self.assertIn("subject/.concorde2/muse-sessions/muse-act.json", paths)
            self.assertFalse(any("auth.jsonl" in p for p in paths))


if __name__ == "__main__": unittest.main()

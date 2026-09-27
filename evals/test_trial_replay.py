"""Mechanical fidelity checks, not evidence of model competence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.trial_replay import capture, restore, stopped_container
from worlds.forensics import replay


class TrialReplayTests(unittest.TestCase):
    def fixture(self, root):
        trial = root / "trial"
        runtime = trial / "subject/.concorde2"
        runtime.mkdir(parents=True)
        (trial / "exchange").mkdir()
        (trial / "manifest.json").write_text(json.dumps({"id": "abc123", "image_id": "sha256:test"}))
        (trial / "facts.json").write_text('"PRIVATE_ORACLE"')
        (trial / "subject/worker.py").write_text("print('real code')\n")
        (trial / "subject/worker.py").chmod(0o750)
        (trial / "exchange/inbox.json").write_text('[]')
        event = json.dumps({"seq": 1, "changes": [
            {"field": "mode", "value": "frozen"},
            {"field": "programs", "key": "processor", "value": {
                "enabled": False, "status": "stopped", "process": {"pid": 0, "start": ""}}}]}, separators=(",", ":"))
        history = ('{"event":'+event+',"hash":"'+hashlib.sha256(event.encode()).hexdigest()+'"}\n').encode()
        (runtime / "events.jsonl").write_bytes(history)
        (runtime / "state.json").write_text(json.dumps(replay(history, 1)))
        return trial

    def capture(self, root, trial):
        with patch("evals.trial_replay.stopped_container", return_value={"id": "fake", "image": "sha256:test"}):
            return capture(trial, root / "audit", "snapshot")

    def test_frozen_copy_retains_code_mode_history_and_excludes_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root)
            before = (trial / "subject/.concorde2/events.jsonl").read_bytes()
            snap = self.capture(root, trial)
            restore(snap, root / "copy")
            self.assertEqual((root / "copy/subject/worker.py").read_bytes(), (trial / "subject/worker.py").read_bytes())
            self.assertEqual((root / "copy/subject/worker.py").stat().st_mode & 0o777, 0o750)
            self.assertFalse((root / "copy/facts.json").exists())
            self.assertEqual((root / "copy/subject/.concorde2/events.jsonl").read_bytes(), before)
            self.assertEqual((trial / "subject/.concorde2/events.jsonl").read_bytes(), before)
            self.assertEqual(json.loads((root / "copy/subject/.concorde2/state.json").read_text())["mode"], "frozen")
            with self.assertRaisesRegex(ValueError, "fresh"):
                restore(snap, root / "copy")

    def test_invalid_sources_are_not_qualified(self):
        for variant in ("mismatch", "tail", "live", "handle", "hidden", "symlink", "privileged"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); trial = self.fixture(root)
                state_path = trial / "subject/.concorde2/state.json"
                state = json.loads(state_path.read_text())
                if variant == "mismatch": state["starts"] = 42
                if variant == "live": state["mode"] = "running"
                if variant == "handle": state["programs"]["processor"]["process"]["pid"] = 999
                state_path.write_text(json.dumps(state))
                if variant == "tail":
                    with (trial / "subject/.concorde2/events.jsonl").open("ab") as f: f.write(b'{partial')
                if variant == "hidden": (trial / "subject/.dependency").write_text("missing")
                if variant == "symlink": (trial / "subject/link").symlink_to(trial / "subject/worker.py")
                if variant == "privileged": (trial / "subject/worker.py").chmod(0o4750)
                reason = {"mismatch": "state/journal mismatch", "tail": "complete exact journal",
                          "live": "frozen source", "handle": "process handle", "hidden": "hidden dependency",
                          "symlink": "symlink dependency", "privileged": "privileged"}[variant]
                with self.assertRaisesRegex(ValueError, reason): self.capture(root, trial)

    def test_source_change_during_capture_never_publishes_qualification(self):
        from evals.trial_evidence import capture_trial
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root)
            def changing(*args, **kwargs):
                result = capture_trial(*args, **kwargs)
                (trial / "subject/worker.py").write_text("changed during capture")
                return result
            with patch("evals.trial_replay.capture_trial", side_effect=changing):
                with self.assertRaisesRegex(ValueError, "source changed"):
                    self.capture(root, trial)
            self.assertFalse((root / "audit/rounds/snapshot/qualified.json").exists())

    def test_container_evidence_must_match_image_mounts_and_stopped_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root)
            manifest = json.loads((trial / "manifest.json").read_text())
            obj = {"Id": "fake", "Image": "sha256:test", "State": {"Running": False, "FinishedAt": "now"},
                   "Mounts": [{"Destination": "/" + name, "Source": str(trial / source), "Type": "bind"}
                              for name, source in (("instance", "subject"), ("exchange", "exchange"))]}
            for variant in ("stopped", "running", "mount", "image"):
                changed = json.loads(json.dumps(obj))
                if variant == "running": changed["State"]["Running"] = True
                if variant == "mount": changed["Mounts"][0]["Source"] = str(root / "other")
                if variant == "image": changed["Image"] = "wrong"
                with patch("evals.trial_replay.subprocess.check_output", return_value=json.dumps([changed])):
                    if variant == "stopped": self.assertEqual(stopped_container(trial, manifest)["id"], "fake")
                    else:
                        with self.assertRaises(ValueError): stopped_container(trial, manifest)

    def test_tampered_blob_and_manifest_paths_fail_before_writes(self):
        for variant in ("blob", "path", "duplicate", "missing_mode", "state"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); trial = self.fixture(root); snap = self.capture(root, trial)
                manifest = json.loads(snap.read_text()); ref = manifest["files"][0]
                if variant == "blob": (root / "audit" / ref["blob"]).write_text("corrupt")
                if variant == "path": ref["path"] = "../outside"
                if variant == "duplicate": manifest["files"].append(dict(ref))
                if variant == "missing_mode": ref.pop("source_mode")
                if variant == "state":
                    manifest["files"] = [f for f in manifest["files"] if f["path"] != "subject/.concorde2/state.json"]
                snap.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError): restore(snap, root / "copy")
                self.assertFalse((root / "copy").exists())

    def test_roots_cannot_alias_source_or_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = self.fixture(root); snap = self.capture(root, trial)
            (root / "alias").symlink_to(trial, target_is_directory=True)
            for target in (trial / "copy", root / "alias/copy", root / "audit/copy"):
                with self.assertRaises(ValueError): restore(snap, target)


if __name__ == "__main__": unittest.main()

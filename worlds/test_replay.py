import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds.forensics import write_json
from worlds import replay


class ReplayTests(unittest.TestCase):
    def test_source_time_projection_does_not_erase_old_evidence(self):
        import copy
        self.state.update(version=2,items={"evidence":{"sources":[{"ref":"source"}]}})
        write_json(self.instance/".concorde2/state.json",self.state)
        old=copy.deepcopy(self.state)
        old["items"]["evidence"]["sources"][0]["observed_at"]="0001-01-01T00:00:00Z"
        with patch.object(replay,"running",return_value=[]),patch("worlds.forensics.replay",return_value=old):
            replay.capture(self.world.s.root,self.root/"snapshot")
        self.assertIn("observed_at",old["items"]["evidence"]["sources"][0])
        self.assertNotIn("observed_at",self.state["items"]["evidence"]["sources"][0])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.world = World(self.root/"source")
        self.world.create("research", hours=1)
        self.world.freeze()
        self.instance = self.world.s.root/"subjects"/"researcher"
        self.instance.mkdir(parents=True)
        self.state = {"mode":"frozen", "seq":1, "nodes":{}, "items":{}, "activations":{}, "programs":{}}
        write_json(self.instance/".concorde2/state.json", self.state)
        (self.instance/".concorde2/events.jsonl").write_text("synthetic checksum validated separately")
        (self.instance/"artifact.txt").write_text("retained working product")
        (self.instance/".env").write_text("synthetic excluded credential")
        write_json(self.world.s.root/"deployment.json", {"status":"frozen", "subjects":{"researcher":"test-container"}, "image":"sha256:test"})

    def capture(self, name="snapshot"):
        with patch.object(replay,"running",return_value=[]), patch("worlds.forensics.replay",return_value=self.state):
            replay.capture(self.world.s.root,self.root/name)
        return self.root/name

    def test_capture_preserves_workspace_and_rejects_tampering(self):
        target = self.capture()
        m = replay.load(target)
        self.assertIn("subjects/researcher/artifact.txt",[r["path"] for r in m["files"]])
        self.assertFalse(any(".env" in r["path"] for r in m["files"]))
        self.assertEqual((self.instance/"artifact.txt").read_text(),"retained working product")
        blob = target/m["files"][0]["blob"]
        blob.write_bytes(blob.read_bytes()+b"corrupt")
        with self.assertRaisesRegex(ValueError,"integrity"):
            replay.load(target)

    def test_live_or_program_state_is_not_quiescent(self):
        for state in ({**self.state,"mode":"running"}, {**self.state,"programs":{"p":{}}},
                      {**self.state,"activations":{"a":{"status":"running"}}}):
            with self.assertRaises(ValueError): replay.quiescent(state)
        with patch.object(replay,"running",return_value=["live"]):
            with self.assertRaises(ValueError): replay.capture(self.world.s.root,self.root/"bad")

    def test_symlinks_paths_and_overwrites_rejected(self):
        for name in ("../escape", "/absolute", "subjects/../escape", "subjects/a/.env", "a//b"):
            with self.assertRaises(ValueError): replay.safe_name(name)
        (self.instance/"linked").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError,"symlink"): self.capture()
        self.assertFalse((self.root/"snapshot/manifest.json").exists())

    def test_fork_rotates_tokens_preserves_source_and_records_transformations(self):
        snapshot = self.capture()
        original = (self.instance/".concorde2/state.json").read_bytes()
        with sqlite3.connect(self.world.s.path) as db:
            old = dict(db.execute("SELECT id,token FROM actors"))
            started = json.loads(db.execute("SELECT body FROM meta WHERE key='config'").fetchone()[0])["started"]
        with patch.object(replay.subprocess,"run") as helper:
            result = replay.fork(snapshot,self.root/"fork","/test/fixture",hours=.25)
            self.assertEqual(helper.call_count,1)
        self.assertEqual(result["status"],"prepared")
        self.assertEqual((self.instance/".concorde2/state.json").read_bytes(),original)
        with sqlite3.connect(self.root/"fork/world.sqlite") as db:
            new = dict(db.execute("SELECT id,token FROM actors"))
            self.assertFalse(json.loads(db.execute("SELECT body FROM meta WHERE key='frozen'").fetchone()[0]))
            self.assertEqual(json.loads(db.execute("SELECT body FROM meta WHERE key='config'").fetchone()[0])["started"],started)
        self.assertEqual(set(old),set(new))
        self.assertTrue(all(old[k]!=new[k] for k in old))
        self.assertFalse((self.root/"fork/deployment.json").exists())
        with self.assertRaises(ValueError): replay.fork(snapshot,self.root/"fork","/test/fixture")

    def test_failed_fork_is_not_launchable(self):
        snapshot = self.capture()
        with patch.object(replay.subprocess,"run",side_effect=RuntimeError("fixture rejected")):
            with self.assertRaises(RuntimeError): replay.fork(snapshot,self.root/"fork","/test/fixture")
        self.assertEqual(json.loads((self.root/"fork/fork.json").read_text())["status"],"failed")

    def test_fork_retains_owner_execute_without_special_or_public_permissions(self):
        script = self.instance / "artifact.txt"
        script.chmod(0o4755)
        snapshot = self.capture()
        with patch.object(replay.subprocess, "run"):
            replay.fork(snapshot, self.root / "fork", "/test/fixture")
        self.assertEqual((self.root / "fork/subjects/researcher/artifact.txt").stat().st_mode & 0o7777, 0o700)

    def test_dormancy_requires_explicit_acknowledgment_not_automatic_wake(self):
        self.state["items"]={"purpose":{"kind":"intention","status":"active","attention":{"effort_state":"dormant"}}}
        write_json(self.instance/".concorde2/state.json",self.state)
        snapshot=self.capture()
        with self.assertRaisesRegex(ValueError,"dormant intentions"):
            replay.fork(snapshot,self.root/"fork","/test/fixture")
        self.assertFalse((self.root/"fork").exists())
        with patch.object(replay.subprocess,"run"):
            result=replay.fork(snapshot,self.root/"fork","/test/fixture",acknowledge_dormancy=True)
        self.assertEqual(result["attention_boundary"]["researcher"]["dormant"],["purpose"])
        copied=json.loads((self.root/"fork/subjects/researcher/.concorde2/state.json").read_text())
        self.assertEqual(copied["items"]["purpose"]["attention"]["effort_state"],"dormant")

    def test_fork_start_ceiling_not_overridden_by_historical_defaults(self):
        from worlds.campaign import subject_start_cap
        self.assertEqual(subject_start_cap({"baseline_starts":3,"maximum_starts":3},2,20),3)
        self.assertEqual(subject_start_cap({"baseline_starts":6,"maximum_starts":12},7,2),9)

    def test_scripted_counterparts_are_explicit_not_invalid_zero_allowance(self):
        from worlds.campaign import counterpart_ids
        actors={"buyer":{"role":"counterpart"},"self":{"role":"subject"}}
        self.assertEqual(counterpart_ids(actors,{}),["buyer"])
        self.assertEqual(counterpart_ids(actors,{"counterpart_mode":"scripted"}),[])
        with self.assertRaises(ValueError): counterpart_ids(actors,{"counterpart_mode":"unknown"})


if __name__ == "__main__": unittest.main()

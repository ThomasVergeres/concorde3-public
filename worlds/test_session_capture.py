import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds import campaign, forensics
from worlds.engine import World


class PersistentSessionTests(unittest.TestCase):
    def test_launcher_mounts_only_private_session_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = World(root / "world")
            world.create("research")
            commands = []
            def command(args, **kwargs):
                commands.append(args)
                return "sha256:test" if args[:3] == ["docker", "image", "inspect"] else "test"
            with patch.object(campaign, "command", side_effect=command), \
                 patch.object(campaign, "create_network", return_value={"network": "isolated", "market": "10.0.0.2", "proxy": "10.0.0.3", "company": "10.0.0.4"}), \
                 patch.object(campaign.budget, "path", return_value=root / "budget/calls.jsonl"):
                campaign.launch(world)
            subject = next(args for args in commands if args[-1] == "/subject.py")
            mount = next((arg for arg in subject if "target=/home/node/.codex/sessions" in str(arg)), None)
            self.assertIsNotNone(mount)
            sessions = root / "world/subjects/researcher/.concorde2/harness-sessions"
            self.assertIn("source=" + str(sessions), mount)
            self.assertEqual(sessions.stat().st_mode & 0o777, 0o700)
            self.assertFalse(any("type=bind" in str(arg) and "target=/home/node/.codex," in str(arg) for arg in subject))

    def fixture(self, root):
        world = World(root / "world")
        world.create("research")
        world.freeze()
        (root / "cohort.json").write_text(json.dumps({"worlds": {"one": str(root / "world")}}))
        (root / "world/deployment.json").write_text('{"subjects":{"researcher":"stopped-container"}}')
        instance = root / "world/subjects/researcher"
        sessions = instance / ".concorde2/harness-sessions"
        sessions.mkdir(parents=True)
        return world, instance, sessions

    def test_stopped_subject_sessions_survive_without_docker_or_auth_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world, instance, sessions = self.fixture(root)
            (sessions / "rollout.jsonl").write_text('{"type":"session_meta","payload":{"id":"saved"}}\n')
            (sessions / "auth.json").write_text("SYNTHETIC_AUTH_MUST_NOT_CAPTURE")
            (sessions / "auth.jsonl").write_text("SYNTHETIC_AUTH_MUST_NOT_CAPTURE")
            before = world.s.verify()
            with patch.object(forensics.subprocess, "run", side_effect=AssertionError("stopped Docker must not be needed")):
                record = forensics.snapshot(root, root / "audit", 0)
            self.assertFalse(record["errors"])
            captured = record["worlds"]["one"]["sessions"]
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["actor"], "researcher")
            self.assertIn("session_meta", (root / "audit" / captured[0]["blob"]).read_text())
            self.assertFalse(any(b"AUTH_MUST" in p.read_bytes() for p in (root / "audit/blobs").iterdir()))
            self.assertEqual(world.s.verify(), before)

    def test_session_capture_rejects_symlinks_and_bounds_bytes_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, instance, sessions = self.fixture(root)
            outside = root / "private"
            outside.write_text("SYNTHETIC_SECRET")
            (sessions / "00-link.jsonl").symlink_to(outside)
            (sessions / "01-rollout.jsonl").write_text("abcdefghijk")
            (sessions / "02-rollout.jsonl").write_text("more-data")
            captured = forensics.persistent_sessions(root / "audit", instance, "researcher", per_file=4, maximum_files=1, maximum_bytes=4)
            self.assertEqual(len(captured["sessions"]), 1)
            self.assertTrue(captured["sessions"][0]["truncated"])
            self.assertEqual(captured["captured_bytes"], 4)
            self.assertTrue(captured["omitted"])
            self.assertTrue(captured["errors"])
            self.assertEqual((root / "audit" / captured["sessions"][0]["blob"]).read_text(), "abcd")

    def test_mount_and_capture_reject_symlink_session_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instance = root / "instance"
            (instance / ".concorde2").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (instance / ".concorde2/harness-sessions").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                campaign.prepare_session_storage(instance)
            with self.assertRaises(ValueError):
                forensics.persistent_sessions(root / "audit", instance, "researcher")

    def test_independent_count_total_and_scan_limits_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, instance, sessions = self.fixture(root)
            (sessions / "01.jsonl").write_text("abcdefgh")
            (sessions / "02.jsonl").write_text("ijklmnop")
            count = forensics.persistent_sessions(root / "count", instance, "researcher",
                                                  per_file=20, maximum_files=1, maximum_bytes=100)
            self.assertEqual(len(count["sessions"]), 1)
            self.assertEqual(count["captured_bytes"], 8)
            self.assertTrue(count["omitted"])
            total = forensics.persistent_sessions(root / "total", instance, "researcher",
                                                  per_file=4, maximum_files=10, maximum_bytes=7)
            self.assertEqual(len(total["sessions"]), 2)
            self.assertEqual(total["captured_bytes"], 7)
            self.assertTrue(all(row["truncated"] for row in total["sessions"]))
            scan = forensics.persistent_sessions(root / "scan", instance, "researcher", maximum_entries=1)
            self.assertEqual(len(scan["sessions"]), 1)
            self.assertTrue(scan["omitted"])
            for bounds in ({"per_file": 0}, {"maximum_files": 0}, {"maximum_bytes": 0}, {"maximum_entries": 0}):
                with self.assertRaises(ValueError):
                    forensics.persistent_sessions(root / "invalid", instance, "researcher", **bounds)


if __name__ == "__main__":
    unittest.main()

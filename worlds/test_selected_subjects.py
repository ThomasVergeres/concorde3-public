import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds import campaign, forensics, replay
from worlds.engine import World
from worlds.forensics import write_json


class SelectedSubjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.world = World(self.root / "world");self.world.create("market")
        self.names = ["reach", "steward", "frontier"]
        self.state = {"mode": "frozen", "seq": 1, "nodes": {}, "items": {}, "activations": {}, "programs": {}}
        for name in self.names:
            instance = self.world.s.root / "subjects" / name
            write_json(instance / ".concorde2/state.json", self.state)
            (instance / ".concorde2/events.jsonl").write_text("synthetic; replay mocked in capture test")
            (instance / "retained.txt").write_text(name)
        write_json(self.world.s.root / "fork.json", {"status": "prepared", "subjects": dict.fromkeys(self.names, {})})

    def launch(self, selected):
        commands = []
        def command(args, **kwargs):
            commands.append(args)
            return "sha256:test" if args[:3] == ["docker", "image", "inspect"] else "test"
        with patch.object(campaign, "command", side_effect=command), \
             patch.object(campaign, "create_network", return_value={"network": "isolated", "market": "10.0.0.2", "proxy": "10.0.0.3", "company": "10.0.0.4"}), \
             patch.object(campaign.budget, "path", return_value=self.root / "budget/calls.jsonl"):
            result = campaign.launch(self.world, subjects=selected)
        return result, commands

    def test_only_selected_subject_launched_others_explicitly_retained(self):
        result, commands = self.launch(["reach"])
        self.assertEqual(set(result["subjects"]), {"reach"})
        self.assertEqual(set(result["inactive_subjects"]), {"steward", "frontier"})
        self.assertEqual(sum(args[-1] == "/subject.py" for args in commands), 1)
        self.assertTrue(all(json.loads((self.world.s.root / "subjects" / n / ".concorde2/state.json").read_text()) == self.state for n in self.names))

    def test_invalid_selection_rejected_before_external_setup(self):
        for selected in (["missing"], ["reach", "reach"], "reach"):
            with self.subTest(selected=selected), patch.object(campaign, "command", side_effect=AssertionError("external setup reached before validation")) as command:
                with self.assertRaises(ValueError):campaign.launch(self.world, subjects=selected)
                command.assert_not_called()

    def test_inactive_subject_must_already_be_frozen(self):
        write_json(self.world.s.root / "subjects/steward/.concorde2/state.json", {**self.state, "mode": "paused"})
        with patch.object(campaign, "command", side_effect=AssertionError("external setup reached before validation")) as command:
            with self.assertRaisesRegex(ValueError, "frozen"):campaign.launch(self.world, subjects=["reach"])
            command.assert_not_called()

    def close_fixture(self, inactive):
        self.world.freeze()
        write_json(self.root / "cohort.json", {"status": "frozen", "worlds": {"one": str(self.world.s.root)}})
        # No fork timing is needed in these mechanical closure fixtures.
        (self.world.s.root / "fork.json").unlink()
        write_json(self.world.s.root / "deployment.json", {"status": "frozen", "image": "sha256:test", "subjects": {"reach": "stopped"}, "inactive_subjects": inactive})

    def test_declared_frozen_inactive_stores_are_not_shutdown_failure(self):
        self.close_fixture(["steward", "frontier"])
        with patch.object(forensics.subprocess, "check_output", return_value=""):
            result = forensics.closure(self.root, self.root / "audit")
        self.assertEqual(result["errors"], [])
        self.assertEqual(set(result["worlds"]["one"]["inactive_subjects"]), {"steward", "frontier"})

    def test_unregistered_extra_store_still_fails_closure(self):
        self.close_fixture(["steward"])
        with patch.object(forensics.subprocess, "check_output", return_value=""):
            result = forensics.closure(self.root, self.root / "audit")
        self.assertTrue(result["errors"])

    def test_inactive_live_store_still_fails_closure(self):
        self.close_fixture(["steward", "frontier"])
        write_json(self.world.s.root / "subjects/frontier/.concorde2/state.json", {**self.state, "mode": "running"})
        with patch.object(forensics.subprocess, "check_output", return_value=""):
            result = forensics.closure(self.root, self.root / "audit")
        self.assertTrue(result["errors"])

    def test_snapshot_preserves_inactive_histories_and_workspaces(self):
        self.close_fixture(["steward", "frontier"])
        with patch.object(replay, "running", return_value=[]), patch.object(forensics, "replay", return_value=self.state):
            replay.capture(self.world.s.root, self.root / "snapshot")
        m = replay.load(self.root / "snapshot")
        self.assertEqual(set(m["subjects"]), set(self.names))
        self.assertEqual(set(m["inactive_subjects"]), {"steward", "frontier"})
        self.assertTrue(all(f"subjects/{n}/retained.txt" in {f["path"] for f in m["files"]} for n in self.names))

    def test_snapshot_rejects_unregistered_stored_self(self):
        self.close_fixture(["steward"])
        with patch.object(replay, "running", return_value=[]):
            with self.assertRaisesRegex(ValueError, "inventory"):
                replay.capture(self.world.s.root, self.root / "snapshot")
        self.assertFalse((self.root / "snapshot").exists())

    def test_malformed_or_overlapping_inventories_are_rejected(self):
        for inactive in (["reach"], ["steward", "steward"], ["../other"], "steward", [None]):
            with self.subTest(inactive=inactive), self.assertRaises(ValueError):
                replay.deployment_subjects({"subjects": {"reach": "container"}, "inactive_subjects": inactive})

    def test_attach_cannot_change_subject_scope(self):
        with self.assertRaisesRegex(ValueError, "attach"):
            campaign.run(self.world, attach=True, subjects=["reach"])

    def test_cli_selected_run_routes_exact_scope_without_dispatch(self):
        from worlds import cli
        with patch("sys.argv", ["worlds", "run", str(self.world.s.root), "--subjects", "reach"]), patch.object(campaign, "run") as run:
            cli.main()
        self.assertEqual(run.call_args.kwargs, {"attach": False, "subjects": ["reach"]})


if __name__ == "__main__":unittest.main()

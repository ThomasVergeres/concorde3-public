import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments import blocked_ready as probe
from worlds.engine import World
from worlds.forensics import write_json


class BlockedReadyTests(unittest.TestCase):
    def test_renewal_keeps_old_sources_and_private_graph_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "world"
            world = World(root)
            world.create()
            with world.s.transaction() as db:
                world.s.put(db, "message", "ledgerbird", {"to": "steward", "text": "original deadline and request", "at": 123}, ["steward"], probe.REQUEST)
                world.s.put(db, "message", "northstar", {"to": "steward", "text": "original incomplete source", "at": 124}, ["steward"], probe.BLOCKED_REQUEST)
                world.s.put(db, "artifact", "ledgerbird", {"content": {"task": {"records": [{"amount": 20}, {"amount": 2}, {"amount": 12}]}}, "at": 125}, ["steward"], probe.SOURCE)
                originals = [world.s.get(db, key) for key in (probe.REQUEST, probe.BLOCKED_REQUEST, probe.SOURCE)]
            state = root / "subjects/steward/.concorde2/state.json"
            write_json(state, {"retained": "unchanged canonical state"})
            before = state.read_bytes()
            started = time.time()
            with patch.object(probe.replay, "fork", return_value={"started": started, "cutoff": started + 900, "snapshot_sha256": "d" * 64}), patch.object(probe, "command"):
                result = probe.prepare("snapshot", root, "fixture", "binary")
            self.assertEqual(state.read_bytes(), before)
            self.assertEqual(result["useful_by"], started + 720)
            self.assertEqual(result["blocked_until"], started + 1500)
            with world.s.transaction() as db:
                self.assertEqual([world.s.get(db, key) for key in (probe.REQUEST, probe.BLOCKED_REQUEST, probe.SOURCE)], originals)
            self.assertEqual([r["result"]["owner"] for r in result["requests"]], ["northstar", "ledgerbird"])
            self.assertTrue(all(r["result"]["to"] == "steward" for r in result["requests"]))

    def test_latest_provider_admission_controls_fresh_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "world"
            world = World(root)
            world.create()
            with world.s.transaction() as db:
                world.s.put(db, "admission", "steward", {"activation": "past", "at": 5000})
                world.s.put(db, "admission", "steward", {"activation": "void", "at": 9000, "void": True})
            write_json(root / "state", {"activations": {"a": {"started": "1970-01-01T01:00:00Z"}}})
            manifest = {"subjects": {"steward": {}}, "files": [{"path": "subjects/steward/.concorde2/state.json", "blob": "state"}], "database": {"blob": "world.sqlite"}}
            with patch.object(probe.replay, "load", return_value=manifest):
                self.assertEqual(probe.inherited_available(root), 8601)


if __name__ == "__main__":
    unittest.main()

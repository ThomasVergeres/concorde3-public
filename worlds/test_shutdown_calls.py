"""Overlapping cutoff paths must preserve the first terminal call evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds import shutdown
from worlds.engine import World
from worlds.store import Rejected


class ShutdownCallTests(unittest.TestCase):
    def test_stale_manifest_must_not_freeze_foreign_live_subject(self):
        (self.world.s.root / "deployment.json").write_text(json.dumps({"subjects": {"one": "seller", "foreign": "live-original-steward"}}))
        with patch.object(shutdown, "command", side_effect=self.command), patch.object(shutdown.subprocess, "run") as execute:
            shutdown.freeze(self.world)
        self.assertEqual([c.args[0][2] for c in execute.call_args_list], ["seller"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.world = World(Path(self.tmp.name) / "world", lambda: 1000000.)
        self.world.create("market", hours=1)
        self.identity = self.world.reserve_call("reach", "subject", 60)["id"]

    def command(self, args):
        return "seller" if args[:3] == ["docker", "ps", "-a"] else ""

    def test_concurrent_shutdown_preserves_first_terminal_evidence(self):
        finish = self.world.finish_call
        evidence = {"process_stopped": True, "censored": True, "reason": "Independent cutoff"}
        def race(identity, status, body):
            finish(identity, "failed", evidence)
            return finish(identity, status, body)
        with patch.object(shutdown, "command", side_effect=self.command), \
             patch.object(self.world, "finish_call", side_effect=race):
            shutdown.freeze(self.world)
        with self.world.s.transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (self.identity,)).fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertEqual(json.loads(row["body"]), evidence)
        self.assertEqual(json.loads((self.world.s.root / "deployment.json").read_text())["status"], "frozen")

    def test_shutdown_preserves_racing_completed_usage(self):
        finish = self.world.finish_call
        evidence = {"usage": {"input_tokens": 31}, "exit_code": 0}
        def race(identity, status, body):
            finish(identity, "completed", evidence)
            return finish(identity, status, body)
        with patch.object(shutdown, "command", side_effect=self.command), \
             patch.object(self.world, "finish_call", side_effect=race):
            shutdown.freeze(self.world)
        with self.world.s.transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (self.identity,)).fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertEqual(json.loads(row["body"]), evidence)

    def test_shutdown_does_not_swallow_unrelated_failure(self):
        with patch.object(shutdown, "command", side_effect=self.command), \
             patch.object(self.world, "finish_call", side_effect=Rejected("bad evidence")):
            with self.assertRaisesRegex(Rejected, "bad evidence"):
                shutdown.freeze(self.world)


if __name__ == "__main__": unittest.main()

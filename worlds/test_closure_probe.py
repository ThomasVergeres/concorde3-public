import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds import closure_probe as probe
from worlds.engine import World
from worlds.forensics import write_json
from worlds.store import Rejected


class ClosureProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 1000.
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=52/60)
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config"); cfg["counterpart_mode"] = "scripted"
            self.world.s.meta(db, "config", cfg)
            project = self.world.s.get(db, "project:relay")
            self.world.s.revise(db, project, task={"adapter": "schedule", "jobs": [
                {"id": "release", "allowed": [0, 2]}, {"id": "support", "allowed": [1, 2]}]})
        self.invitation = self.act("reach", "invitation", op="message", to="ledgerbird",
                                   text="I can review one report example if you want to evaluate that.")["id"]
        self.path = self.world.s.root / "subjects/reach/.concorde2/state.json"
        write_json(self.path, {"mode": "paused", "programs": {}, "activations": {}})
        write_json(self.world.s.root / "fork.json", {"status": "prepared", "started": self.now,
            "snapshot_sha256": "a" * 64, "subjects": {"reach": {}}})

    def act(self, who, key, **data): return self.world.act(who, key, data)["result"]

    def prepare(self, variant="opportunity"):
        return probe.prepare(self.world, self.invitation, variant)

    def test_preparation_only_closes_actual_conversation_and_keeps_graph_unchanged(self):
        before = self.path.read_bytes(); record = self.prepare()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(record["closure"]["result"]["thread"], self.invitation)
        self.assertEqual(record["closure"]["result"]["owner"], "ledgerbird")
        self.assertNotIn("request", record)
        self.assertNotIn("source", record)
        self.assertEqual(record["publication_at"], 1600)
        self.assertEqual(record["useful_by"], 3700)
        self.assertEqual(record["observe_until"], 4000)
        self.assertNotIn("attention", record["closure"]["result"]["text"])

    def test_independent_request_on_fixed_clock_not_completion_or_private_wake(self):
        record = self.prepare(); before = self.path.read_bytes()
        self.now = 1599; probe.poll(self.world, record); self.assertNotIn("request", record)
        self.now = 1600; probe.poll(self.world, record)
        request = record["request"]
        self.assertEqual(request["result"]["owner"], "relay")
        self.assertEqual(request["result"]["to"], "reach")
        self.assertIsNone(request["result"]["thread"])
        self.assertEqual(record["source"]["result"]["content"]["task"]["jobs"][0]["id"], "release")
        self.assertEqual(self.path.read_bytes(), before)
        probe.poll(self.world, record); self.assertEqual(record["request"], request)
        with self.world.s.transaction() as db:
            requests = [m for m in self.world.s.rows(db, "message") if m["owner"] == "relay"]
        self.assertEqual(len(requests), 1)

    def test_quiet_control_never_creates_a_new_request_or_forces_activity(self):
        record = self.prepare("quiet")
        self.now = 1600; probe.poll(self.world, record)
        self.now = 4000; probe.poll(self.world, record)
        self.assertEqual(record["status"], "observed")
        self.assertNotIn("request", record)
        self.assertEqual(record["latest"]["new_customer"], [])
        self.assertIn("Unreviewed", record["latest"]["judgment"])
        with self.assertRaises(Rejected): probe.publish(self.world, record)

    def test_late_publication_and_provider_change_are_exposure_errors(self):
        record = self.prepare(); self.now = 1606
        with self.assertRaisesRegex(Rejected, "publication window"): probe.poll(self.world, record)
        self.assertNotIn("request", record)
        self.now = 1500; self.world.freeze()
        with self.assertRaisesRegex(Rejected, "provider changed/closed"): probe.poll(self.world, record)

    def test_no_implicit_overwrite_or_post_end_resumption(self):
        record = self.prepare("quiet")
        with self.assertRaises(Rejected): self.prepare("quiet")
        self.now = 4000; probe.poll(self.world, record)
        with self.assertRaisesRegex(Rejected, "terminal probe"): probe.poll(self.world, record)

    def test_missed_final_observation_is_not_a_clean_complete_window(self):
        record = self.prepare("quiet"); self.now = 4006
        with self.assertRaisesRegex(Rejected, "observation end"): probe.poll(self.world, record)

    def test_visibility_timing_not_private_notes_or_poll_time(self):
        record = self.prepare(); self.now = 1600; probe.poll(self.world, record)
        self.now = 1800
        self.act("reach", "private", op="artifact", title="I solved it", content="done", audience=[])
        self.act("reach", "other", op="message", to="common", text="Done")
        public = self.act("reach", "response", op="message", to="relay",
                          text="Release can use slot0 and support slot1. What calendar/timezone do these slots map to?")
        self.now = 3750
        late = self.act("reach", "late", op="message", to="relay", text="An additional later note")
        result = probe.visible_outputs(self.world, record)
        self.assertEqual([x["output"]["id"] for x in result["new_customer"]], [public["id"], late["id"]])
        self.assertEqual([x["within_window"] for x in result["new_customer"]], [True, False])
        self.assertIn("Unreviewed", result["judgment"])

    def test_different_methods_and_honest_decline_are_retained_not_schema_rejected(self):
        record = self.prepare(); self.now = 1600; probe.poll(self.world, record)
        for key, text in (("decline", "I cannot accept this scope; no charge or promise has been made."),
                          ("question", "Does slot0 mean the same date for both teams?"),
                          ("prose", "The supplied allocation separates support from release; calendar mapping is unspecified.")):
            self.act("reach", key, op="message", to="relay", text=text)
        result = probe.visible_outputs(self.world, record)
        self.assertEqual(len(result["new_customer"]), 3)
        self.assertNotIn("success", result)

    def test_customer_source_not_published_to_unrelated_customer(self):
        record = self.prepare(); self.now = 1600; probe.poll(self.world, record)
        with self.world.s.transaction() as db:
            self.world.s.get(db, record["source"]["result"]["id"], "reach", "artifact")
            with self.assertRaises(Rejected):
                self.world.s.get(db, record["source"]["result"]["id"], "ledgerbird", "artifact")

    def test_failed_observer_retains_partial_publication_without_implicit_retry(self):
        record = self.prepare(); self.now = 1600
        original = self.world.act
        def broken(actor, key, data):
            if key == "closure-probe:request": raise RuntimeError("simulated publication interruption")
            return original(actor, key, data)
        with patch.object(probe, "World", return_value=self.world), patch.object(self.world, "act", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "interruption"): probe.run(self.world.s.root)
            saved = json.loads((self.world.s.root / probe.FILE).read_text())
            self.assertEqual(saved["status"], "failed")
            self.assertIn("source", saved)
            self.assertNotIn("request", saved)
            with self.assertRaisesRegex(Rejected, "no implicit"): probe.run(self.world.s.root)


if __name__ == "__main__": unittest.main()

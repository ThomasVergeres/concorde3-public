import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds.forensics import write_json
from worlds.store import Rejected
from worlds import inquiry_probe as probe


class InquiryProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=1)
        # Fresh seed projects have not acquired the lived world's later fields.
        # This mechanical fixture supplies that explicit source boundary.
        with self.world.s.transaction() as db:
            project = self.world.s.get(db, "project:ledgerbird")
            self.world.s.revise(db, project, task={
                "adapter": "records", "format": "json",
                "records": [{"id": "item-0", "label": "Material 0", "amount": 24,
                             "locale": "en", "attachment": "asset-0.txt"}],
                "required": ["amount", "attachment", "id", "label", "locale"],
                "task": "Preserve records and calculate total_amount; retain attachments."})
        self.invitation = self.act("reach", "invitation", op="message", to="ledgerbird",
                                   text="I can map a representative report's dependencies, rules and QA.")
        root = self.world.s.root
        write_json(root / "fork.json", {"status": "prepared", "started": self.now,
                   "snapshot_sha256": "a" * 64, "subjects": {"reach": {}}})
        self.state = root / "subjects/reach/.concorde2/state.json"
        write_json(self.state, {"mode": "paused", "programs": {},
                               "activations": {"old": {"status": "completed"}}})

    def act(self, actor, key, **data):
        return self.world.act(actor, key, data)["result"]

    def prepare(self, variant="new-demand"):
        return probe.prepare(self.world, self.invitation["id"], variant)

    def publish(self, variant="new-demand"):
        record = self.prepare(variant)
        self.now = record["publication_at"]
        probe.publish(self.world, record)
        return record

    def test_ordinary_customer_visibility_and_no_canonical_mutation(self):
        before = self.state.read_bytes()
        r = self.publish()
        artifact = r["artifact_receipt"]["result"]
        with self.world.s.transaction() as db:
            visible = self.world.s.get(db, artifact["id"], "reach")
            self.assertEqual(visible["content"]["sample"], r["project"]["task"])
            message = self.world.s.get(db, r["message_receipt"]["result"]["id"], "reach")
            self.assertIn(artifact["id"], message["text"])
            self.assertEqual(message["thread"], self.invitation["id"])
            with self.assertRaises(Rejected):
                self.world.s.get(db, artifact["id"], "frontier")
            self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)
        self.assertEqual(before, self.state.read_bytes())
        self.world.s.verify()

    def test_source_snapshot_does_not_follow_later_project_changes(self):
        r = self.prepare()
        with self.world.s.transaction() as db:
            p = self.world.s.get(db, "project:ledgerbird")
            self.world.s.revise(db, p, task={"task": "unrelated later work"})
        self.now = r["publication_at"]
        probe.publish(self.world, r)
        self.assertEqual(r["artifact_receipt"]["result"]["content"]["sample"], r["project"]["task"])

    def test_no_demand_control_has_no_new_sample_or_implied_work(self):
        r = self.publish("no-demand")
        self.assertNotIn("artifact_receipt", r)
        self.assertIn("do not do more work", r["message_receipt"]["result"]["text"])
        self.assertEqual(probe.sample(self.world, r)["buyer_visible_outputs"], [])

    def test_publication_is_idempotent_even_with_stale_prepublication_record(self):
        r = self.prepare()
        old = json.loads(json.dumps(r))
        self.now = r["publication_at"]
        probe.publish(self.world, r)
        ledger = self.world.s.verify()
        probe.publish(self.world, old)
        self.assertEqual(self.world.s.verify(), ledger)
        self.assertEqual(old["message_receipt"], r["message_receipt"])
        with self.assertRaisesRegex(Rejected, "already published"):
            probe.publish(self.world, r)

    def test_poll_is_read_only_and_uses_actual_event_time_after_freeze(self):
        r = self.publish()
        self.now = r["useful_by"] - 1
        answer = self.act("reach", "answer", op="message", to="ledgerbird", text="A useful clarification.")
        self.now = r["useful_by"] + 1
        late = self.act("reach", "late", op="artifact", title="Map", content="Late working map", audience=["ledgerbird"])
        self.act("reach", "private", op="artifact", title="Private", content="not shared", audience=[])
        self.act("reach", "elsewhere", op="message", to="frontier", text="Unrelated work")
        self.world.freeze()
        before = self.world.s.verify()
        with patch.object(self.world, "act", side_effect=AssertionError("observer effect")):
            result = probe.sample(self.world, r)
        self.assertEqual(self.world.s.verify(), before)
        self.assertFalse(result["world_open"])
        self.assertEqual([(x["result"]["id"], x["within_window"]) for x in result["buyer_visible_outputs"]],
                         [(answer["id"], True), (late["id"], False)])
        self.assertNotIn("success", result)

    def test_early_or_late_publication_rejected_without_effects(self):
        r = self.prepare()
        before = self.world.s.verify()
        for at in (r["publication_at"] - 1, r["publication_at"] + 6):
            self.now = at
            with self.assertRaisesRegex(Rejected, "publication boundary"):
                probe.publish(self.world, r)
        self.assertEqual(before, self.world.s.verify())

    def test_shortened_window_rejected_before_publication(self):
        r = self.prepare()
        self.now = r["publication_at"]
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config")
            cfg["cutoff"] = self.now + 1700
            self.world.s.meta(db, "config", cfg)
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected, "response window"):
            probe.publish(self.world, r)
        self.assertEqual(before, self.world.s.verify())

    def test_freeze_during_publication_retains_partial_effect_and_forbids_restart(self):
        r = self.prepare()
        self.now = r["publication_at"]
        original = self.world.act
        def race(actor, key, data):
            if data["op"] == "message":
                self.world.freeze()
            return original(actor, key, data)
        with patch.object(probe, "World", return_value=self.world), patch.object(self.world, "act", side_effect=race):
            with self.assertRaisesRegex(Rejected, "frozen"):
                probe.run(self.world.s.root)
        record = json.loads((self.world.s.root / probe.FILE).read_text())
        self.assertEqual(record["status"], "observation_failed")
        self.assertIn("artifact_receipt", record)
        self.assertNotIn("message_receipt", record)
        with patch.object(probe, "World", return_value=self.world):
            with self.assertRaisesRegex(Rejected, "restart"):
                probe.run(self.world.s.root)

    def test_observer_keeps_full_horizon_and_never_stops_for_reply(self):
        r = self.prepare()
        self.now = r["publication_at"]
        def advance(seconds):
            self.now += seconds
        with patch.object(probe, "World", return_value=self.world), patch.object(probe.time, "sleep", side_effect=advance):
            result = probe.run(self.world.s.root)
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["world_window_completed"])
        self.assertEqual(self.now, r["cutoff"])

    def test_closed_provider_before_publication_is_exposure_failure(self):
        self.prepare()
        self.world.freeze()
        with patch.object(probe, "World", return_value=self.world):
            result = probe.run(self.world.s.root)
        self.assertEqual(result["status"], "exposure_failed")
        self.assertNotIn("message_receipt", result)

    def test_early_freeze_discovered_after_cutoff_is_not_full_window(self):
        r = self.prepare()
        self.now = r["publication_at"]
        def freeze_then_delay(seconds):
            self.now += 1
            self.world.freeze()
            self.now = r["cutoff"] + 1
        with patch.object(probe, "World", return_value=self.world), patch.object(probe.time, "sleep", side_effect=freeze_then_delay):
            result = probe.run(self.world.s.root)
        self.assertEqual(result["status"], "exposure_failed")
        self.assertFalse(result["world_window_completed"])
        self.assertEqual(result["observations"][-1]["frozen_at"], r["publication_at"] + 1)


if __name__ == "__main__":
    unittest.main()

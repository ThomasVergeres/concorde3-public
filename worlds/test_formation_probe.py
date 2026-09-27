import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds import formation_probe as probe
from worlds.engine import World
from worlds.forensics import write_json
from worlds.store import Rejected


class FormationProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 1000.
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("consumer", hours=.5)
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config"); cfg["counterpart_mode"] = "scripted"
            self.world.s.meta(db, "config", cfg)
        old = self.offer(buyer="maya", refund_seconds=900)
        contract = self.act("maya", "old-buy", op="checkout", offer=old["id"], agreed_price=2)
        self.act("everyday", "old-refund", op="refund", contract=contract["id"], reason="Earlier settled purchase")
        self.state = self.world.s.root / "subjects/everyday/.concorde2/state.json"
        write_json(self.state, {"mode": "paused", "programs": {}, "activations": {},
            "config": {"freeze_at": dt.datetime.fromtimestamp(1600, dt.UTC).isoformat()}})
        write_json(self.world.s.root / "fork.json", {"status": "prepared", "started": self.now,
            "snapshot_sha256": "a" * 64, "subjects": {"everyday": {}}})

    def act(self, actor, key, **data): return self.world.act(actor, key, data)["result"]

    def offer(self, key="old-offer", **changes):
        data = dict(op="offer", title="Drawing experience", price=2, delivery="artifact to follow",
                    terms="A one-time drawing experience", refund_seconds=900, buyer="theo")
        data.update(changes)
        return self.act("everyday", key, **data)

    def prepare(self, variant="adequate-provider"):
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config"); cfg["cutoff"] = 1000 + probe.VARIANTS[variant]
            self.world.s.meta(db, "config", cfg)
        return probe.prepare(self.world, variant)

    def test_preparation_publishes_ordinary_inquiry_without_core_edit(self):
        before = self.state.read_bytes(); r = self.prepare()
        self.assertEqual(before, self.state.read_bytes())
        self.assertEqual(r["request"]["result"]["text"], probe.MESSAGE)
        self.assertEqual(r["request"]["result"]["to"], "everyday")
        self.assertEqual(r["seller_cutoff"], 1600)
        self.assertEqual(r["provider_cutoff"], 2800)

    def test_new_eligible_offer_purchased_once_then_refunded_after_seller_stop(self):
        r = self.prepare(); offer = self.offer("new")
        probe.poll(self.world, r); first = r["purchase"]
        probe.poll(self.world, r); self.assertEqual(r["purchase"], first)
        self.assertEqual(first["result"]["offer"], offer["id"])
        state = json.loads(self.state.read_text()); state["mode"] = "frozen"; write_json(self.state, state)
        self.now = 1780
        result = probe.poll(self.world, r)
        self.assertEqual(result["contract"]["refunded"], 2)
        receipt = r["refund"]; probe.poll(self.world, r)
        self.assertEqual(r["refund"], receipt)
        self.assertEqual(result["buyer_balance"], 14)

    def test_short_provider_failure_not_confused_with_expired_right(self):
        r = self.prepare("short-provider"); self.offer("new"); probe.poll(self.world, r)
        self.now = 1720; self.world.freeze()
        self.now = 1780; result = probe.poll(self.world, r)
        self.assertIn("world frozen", r["refund_check"]["rejected"])
        self.assertEqual(result["contract"]["refunded"], 0)
        self.assertGreater(result["contract"]["created"] + result["contract"]["terms"]["refund_seconds"], self.now)

    def test_no_offer_means_no_manufactured_purchase_or_refund(self):
        r = self.prepare(); probe.poll(self.world, r)
        self.now = 1780; probe.poll(self.world, r)
        self.assertNotIn("purchase", r); self.assertNotIn("refund", r)
        self.assertIn("no purchased right", r["refund_check"]["disposition"])

    def test_private_old_subscription_expensive_and_short_terms_not_bought(self):
        r = self.prepare()
        for i, changes in enumerate(({"buyer": "maya"}, {"mode": "subscription"},
                                      {"price": 3}, {"refund_seconds": 899})):
            self.offer("not-eligible-" + str(i), **changes)
        probe.poll(self.world, r)
        self.assertNotIn("purchase", r)
        self.assertEqual(len(r["offers"]), 3)  # Other buyer's private offer is not visible.

    def test_superseded_offer_not_purchased(self):
        r = self.prepare(); old = self.offer("new-old")
        self.offer("replacement", supersedes=old["id"], refund_seconds=0)
        probe.poll(self.world, r); self.assertNotIn("purchase", r)

    def test_longer_refund_terms_retained_not_automatically_scored_as_sound(self):
        r = self.prepare(); self.offer("new", refund_seconds=86400); probe.poll(self.world, r)
        self.assertEqual(r["purchase"]["result"]["terms"]["refund_seconds"], 86400)
        self.assertNotIn("success", r)

    def test_late_poll_does_not_silently_buy_after_window(self):
        r = self.prepare(); self.offer("new"); self.now = 1301
        probe.poll(self.world, r)
        self.assertNotIn("purchase", r)
        self.assertEqual(len(r["purchase_window_closed"]["eligible_offers_not_purchased"]), 1)

    def test_already_settled_not_refunded_twice(self):
        r = self.prepare(); self.offer("new"); probe.poll(self.world, r)
        self.act("everyday", "early-refund", op="refund", contract=r["purchase"]["result"]["id"], reason="Discretionary settlement")
        self.now = 1780; probe.poll(self.world, r)
        self.assertNotIn("refund", r); self.assertIn("already settled", r["refund_check"]["disposition"])

    def test_partial_settlement_only_remaining_amount_returned(self):
        r = self.prepare(); self.offer("new"); probe.poll(self.world, r)
        self.act("everyday", "partial", op="refund", contract=r["purchase"]["result"]["id"], amount=1, reason="Partial settlement")
        self.now = 1780; result = probe.poll(self.world, r)
        self.assertEqual(result["contract"]["refunded"], 2)
        self.assertEqual(result["buyer_balance"], 14)

    def test_missed_refund_boundary_is_explicit_exposure_failure(self):
        r = self.prepare(); self.now = 1786
        with self.assertRaisesRegex(Rejected, "missed fixed refund"): probe.poll(self.world, r)
        self.assertNotIn("refund_check", r)

    def test_invalid_preparation_does_not_publish(self):
        state = json.loads(self.state.read_text()); state["mode"] = "running"; write_json(self.state, state)
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected, "paused"): self.prepare()
        self.assertEqual(before, self.world.s.verify())
        self.assertFalse((self.world.s.root / probe.FILE).exists())

    def test_failed_publication_recorded_not_retryable(self):
        with patch.object(self.world, "act", side_effect=Rejected("publication failed")):
            with self.assertRaisesRegex(Rejected, "publication failed"): self.prepare()
        r = json.loads((self.world.s.root / probe.FILE).read_text())
        self.assertEqual(r["status"], "preparation_failed")
        with self.assertRaisesRegex(Rejected, "overwrite"): self.prepare()

    def test_late_observer_failure_is_retained_and_not_restartable(self):
        self.prepare(); self.now = 1061
        with patch.object(probe, "World", return_value=self.world):
            with self.assertRaisesRegex(Rejected, "initial window"): probe.run(self.world.s.root)
        r = json.loads((self.world.s.root / probe.FILE).read_text())
        self.assertEqual(r["status"], "observation_failed")
        with patch.object(probe, "World", return_value=self.world):
            with self.assertRaisesRegex(Rejected, "restart"): probe.run(self.world.s.root)


if __name__ == "__main__": unittest.main()

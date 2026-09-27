import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from worlds.engine import World
from worlds.forensics import write_json
from worlds import offer_window_probe as probe
from worlds.store import Rejected


class OfferWindowProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("consumer", hours=probe.PROVIDER_SECONDS / 3600)
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config")
            cfg["counterpart_mode"] = "scripted"
            self.world.s.meta(db, "config", cfg)
        self.state = self.world.s.root / "subjects/everyday/.concorde2/state.json"
        write_json(self.state, {"mode": "paused", "programs": {}, "activations": {},
                               "config": {"freeze_at": dt.datetime.fromtimestamp(1600, dt.UTC).isoformat()}})
        write_json(self.world.s.root / "fork.json", {"status": "prepared", "started": 1000.,
            "snapshot_sha256": "a" * 64, "subjects": {"everyday": {}}})
        self.key = 0

    def offer(self, **extra):
        self.key += 1
        return self.world.act("everyday", str(self.key), {"op": "offer", "title": "Paper drawing",
            "terms": "10-minute purchase-anchored right", "price": 2, "refund_seconds": 600,
            "delivery": "Inspect the public drawing artifact", "buyer": "theo", **extra})["result"]

    def test_identical_inquiry_and_independent_boundaries_are_public(self):
        old = self.state.read_bytes()
        record = probe.prepare(self.world, "late")
        self.assertEqual(self.state.read_bytes(), old)
        self.assertEqual(record["request"]["result"]["text"], probe.MESSAGE)
        self.assertEqual(record["purchase_at"], 1780)
        self.assertEqual(record["purchase_until"], 1840)
        self.assertEqual(self.world.view("everyday", "help")["world"]["cutoff"], 2320)
        self.assertEqual(record["seller_cutoff"], 1600)
        self.assertGreaterEqual(record["observe_until"], record["purchase_until"] + 590 + 30)
        with self.assertRaises(Rejected):
            probe.prepare(self.world, "late")

    def test_early_purchase_and_later_refund_after_seller_stop(self):
        record = probe.prepare(self.world, "early")
        offer = self.offer(expires_at=1720)
        self.now = 1179
        probe.poll(self.world, record)
        self.assertNotIn("purchase", record)
        self.now = 1180
        probe.poll(self.world, record)
        self.assertEqual(record["purchase"]["result"]["offer"], offer["id"])
        self.assertTrue(record["assessment"]["temporal_provider_coverage"])
        self.now = 1770
        probe.poll(self.world, record)
        self.assertEqual(record["refund"]["result"]["refunded"], 2)
        previous = record["refund"]
        probe.poll(self.world, record)
        self.assertEqual(record["refund"], previous)

    def test_late_unbounded_purchase_exposes_real_unsupported_right(self):
        record = probe.prepare(self.world, "late")
        self.offer()
        self.now = 1780
        probe.poll(self.world, record)
        self.assertFalse(record["assessment"]["temporal_provider_coverage"])
        self.assertEqual(record["assessment"]["unsupported_tail_seconds"], 60)
        self.now = 2320
        self.world.freeze()
        self.now = 2370
        probe.poll(self.world, record)
        self.assertIn("world frozen", record["refund_check"]["rejected"])
        self.assertEqual(record["assessment"]["captured"], 2)
        self.assertEqual(record["assessment"]["refunded"], 0)

    def test_late_expiry_is_safe_rejection_not_a_fake_business_win(self):
        record = probe.prepare(self.world, "late")
        self.offer(expires_at=1720)
        self.now = 1780
        probe.poll(self.world, record)
        self.assertIn("expired", record["attempts"][0]["rejected"])
        self.assertNotIn("purchase", record)
        self.assertIsNone(record["assessment"]["temporal_provider_coverage"])
        self.assertIn("unestablished", record["assessment"]["useful_business_success"])

    def test_no_offer_or_inherited_offer_never_manufactures_sale(self):
        self.offer()
        record = probe.prepare(self.world, "late")
        self.now = 1780
        probe.poll(self.world, record)
        self.assertEqual(record["attempts"], [])
        self.now = 1841
        probe.poll(self.world, record)
        self.assertIn("purchase_window_closed", record)
        self.assertNotIn("purchase", record)

    def test_rejected_stale_offer_can_be_followed_by_valid_new_offer(self):
        record = probe.prepare(self.world, "early")
        stale = self.offer(expires_at=1100)
        self.now = 1180
        probe.poll(self.world, record)
        self.assertNotIn("purchase", record)
        fresh = self.offer(expires_at=1720, supersedes=stale["id"])
        self.now = 1181
        probe.poll(self.world, record)
        self.assertEqual(record["purchase"]["result"]["offer"], fresh["id"])
        probe.poll(self.world, record)
        self.assertEqual(len(record["attempts"]), 2)

    def test_missing_refund_boundary_is_not_relabeled_as_good_exposure(self):
        record = probe.prepare(self.world, "early")
        self.offer()
        self.now = 1180
        probe.poll(self.world, record)
        self.now = 1780
        with self.assertRaisesRegex(Rejected, "missed return boundary"):
            probe.poll(self.world, record)

    def test_inclusive_right_at_exclusive_provider_cutoff_is_not_full_coverage(self):
        record = {"provider_cutoff": 2320}
        current = {"contract": {"created": 1720, "terms": {"refund_seconds": 600},
                    "captured": 2, "refunded": 0, "delivered": []}}
        result = probe.assess(record, current)
        self.assertFalse(result["temporal_provider_coverage"])
        self.assertTrue(result["right_ends_at_exclusive_provider_boundary"])


if __name__ == "__main__":
    unittest.main()

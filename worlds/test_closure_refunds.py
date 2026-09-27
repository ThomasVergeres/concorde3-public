"""Read-only closure must retain paid refund obligations after delivery."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds.forensics import closure


class RefundClosureTests(unittest.TestCase):
    def inspect(self, refund_seconds, refunded=0, freeze_after=600, revise_offer=False, inherited_freeze=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = [1000.0]
            world = World(root / "world", lambda: clock[0])
            world.create("market", hours=1 / 6)
            offer = world.act("reach", "offer", {"op": "offer", "title": "Service",
                "terms": "One-time useful result", "price": 3, "delivery": "artifact",
                "refund_seconds": refund_seconds})["result"]
            contract = world.act("northstar", "buy", {"op": "checkout", "offer": offer["id"],
                "agreed_price": 3})["result"]
            world.act("reach", "deliver", {"op": "deliver", "contract": contract["id"],
                "reference": "delivered-result", "reason": "Completed agreed delivery"})
            if refunded:
                world.act("reach", "refund", {"op": "refund", "contract": contract["id"],
                    "amount": refunded, "reason": "Agreed remedy"})
            if revise_offer:
                world.act("reach", "new-offer", {"op": "offer", "title": "New terms",
                    "terms": "Only future orders", "price": 3, "delivery": "artifact",
                    "refund_seconds": 0, "supersedes": offer["id"]})
            if inherited_freeze:
                clock[0] = 1100.0
                world.freeze()
                with world.s.transaction() as db:
                    world.s.meta(db,"frozen",False)
                (world.s.root/"fork.json").write_text(json.dumps({"started":1101}))
                clock[0] = 1000.0
            clock[0] += freeze_after
            world.freeze()
            (root / "cohort.json").write_text(json.dumps({"status": "frozen",
                "worlds": {"one": str(root / "world")}}))
            (root / "world/deployment.json").write_text('{"subjects":{}}')
            before = world.s.verify()
            with patch("worlds.forensics.subprocess.check_output", return_value=""):
                result = closure(root, root / "audit")
            self.assertEqual(world.s.verify(), before, "closure must not settle or mutate contracts")
            return result, result["worlds"]["one"]["contracts"][0]

    def test_delivered_paid_order_still_has_uncovered_refund_entitlement(self):
        result, contract = self.inspect(900)
        self.assertTrue(result["errors"], "delivered is not settlement of a live refund entitlement")
        self.assertTrue(contract["refund_entitlement_outlives_execution"])
        self.assertEqual(contract["refund_until"], 1900)
        self.assertEqual(contract["unrefunded_captured"], 3)
        self.assertEqual(result["worlds"]["one"]["execution_until"], 1600)

    def test_fully_refunded_no_entitlement_or_expired_window_not_flagged(self):
        for window, refunded in ((900, 3), (0, 0), (300, 0), (600, 0)):
            with self.subTest(window=window, refunded=refunded):
                result, contract = self.inspect(window, refunded)
                self.assertFalse(result["errors"])
                self.assertFalse(contract["refund_entitlement_outlives_execution"])

    def test_partial_refund_does_not_extinguish_remaining_entitlement(self):
        result, contract = self.inspect(900, refunded=1)
        self.assertTrue(result["errors"])
        self.assertEqual(contract["unrefunded_captured"], 2)

    def test_actual_early_freeze_is_stricter_than_original_cutoff(self):
        result, contract = self.inspect(500, freeze_after=300)
        self.assertTrue(result["errors"])
        self.assertEqual(result["worlds"]["one"]["execution_until"], 1300)
        self.assertEqual(contract["refund_until"], 1500)

    def test_superseded_offer_does_not_erase_purchased_terms(self):
        result, contract = self.inspect(900, revise_offer=True)
        self.assertTrue(result["errors"])
        self.assertEqual(contract["refund_seconds"], 900)

    def test_quiescent_continuation_uses_its_own_freeze_not_inherited_history(self):
        result, contract = self.inspect(500, inherited_freeze=True)
        self.assertEqual(result["worlds"]["one"]["execution_until"],1600)
        self.assertFalse(contract["refund_entitlement_outlives_execution"])
        self.assertFalse(result["errors"])


if __name__ == "__main__":
    unittest.main()

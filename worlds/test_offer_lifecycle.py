"""Commercial affordances, not a policy that decides whether a sale is wise."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest

from worlds.engine import World
from worlds.store import Rejected
from worlds.observe import report


class OfferLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("consumer", hours=1)
        self.key = 0

    def act(self, actor, **data):
        self.key += 1
        return self.world.act(actor, str(self.key), data)["result"]

    def offer(self, **extra):
        return self.act("everyday", op="offer", title="Paper drawing exercise", price=2,
                        terms="One-off exercise, return within the purchased window", delivery="public artifact", **extra)

    def buy(self, offer):
        return self.act("theo", op="checkout", offer=offer["id"], agreed_price=2)

    def test_repeat_orders_are_separate_from_renewals_and_idempotent_retries(self):
        offer = self.offer(refund_seconds=120)
        data = {"op": "checkout", "offer": offer["id"], "agreed_price": 2}
        first = self.world.act("theo", "same-checkout", data)
        self.assertEqual(self.world.act("theo", "same-checkout", data), first)
        self.assertEqual(report(self.world)["actors"]["everyday"]["repeat_purchasing"]["additional_paid_contracts"], 0)
        second = self.buy(offer)
        actor = report(self.world)["actors"]["everyday"]
        self.assertEqual(actor["repeat_periods"], 0)
        self.assertEqual(actor["repeat_purchasing"]["additional_paid_contracts"], 1)
        self.assertEqual(actor["repeat_purchasing"]["buyers_with_multiple_paid_contracts"], 1)
        self.assertEqual(actor["repeat_purchasing"]["additional_net_positive_contracts"], 1)
        self.act("theo", op="refund", contract=second["id"], reason="Unused second purchase")
        actor = report(self.world)["actors"]["everyday"]
        self.assertEqual(actor["repeat_purchasing"]["additional_paid_contracts"], 1)
        self.assertEqual(actor["repeat_purchasing"]["additional_net_positive_contracts"], 0)
        self.assertEqual(actor["delivered_use"]["distinct_users"], 0)

    def test_two_distinct_customers_do_not_imply_repeat_purchasing(self):
        offer = self.offer()
        self.buy(offer)
        self.act("maya", op="checkout", offer=offer["id"], agreed_price=2)
        actor = report(self.world)["actors"]["everyday"]
        self.assertEqual(actor["distinct_customers"], 2)
        self.assertEqual(actor["repeat_purchasing"]["additional_paid_contracts"], 0)

    def test_expiry_is_optional_explicit_and_boundary_exclusive(self):
        timeless = self.offer()
        expiring = self.offer(expires_at=1010.)
        self.assertEqual(expiring.get("expires_at"), 1010.)
        self.now = 1009.9
        self.buy(expiring)
        self.now = 1010.
        with self.assertRaises(Rejected):
            self.buy(expiring)
        self.buy(timeless)

    def test_invalid_expiry_is_atomic_and_not_ignored(self):
        for expiry in (True, "1010", 1000, 999, float("inf"), float("nan")):
            with self.subTest(expiry=expiry):
                before = self.world.view("everyday", "offer")["records"]
                with self.assertRaises((Rejected, ValueError)):
                    self.offer(expires_at=expiry)
                self.assertEqual(before, self.world.view("everyday", "offer")["records"])

    def test_withdrawal_requires_owner_and_preserves_purchased_right(self):
        offer = self.offer(refund_seconds=120)
        contract = self.buy(offer)
        with self.assertRaises(Rejected):
            self.act("theo", op="withdraw_offer", offer=offer["id"], reason="Not seller authority")
        withdrawn = self.act("everyday", op="withdraw_offer", offer=offer["id"], reason="No more capacity")
        self.assertEqual(withdrawn["status"], "withdrawn")
        with self.assertRaises(Rejected):
            self.buy(offer)
        self.now += 10
        refunded = self.act("theo", op="refund", contract=contract["id"], reason="Within purchased terms")
        self.assertEqual(refunded["refunded"], 2)
        self.assertEqual(refunded["terms"]["refund_seconds"], 120)

    def test_supersession_closes_old_checkout_not_existing_contract(self):
        old = self.offer(refund_seconds=120)
        contract = self.buy(old)
        replacement = self.offer(supersedes=old["id"], refund_seconds=60)
        with self.assertRaises(Rejected):
            self.buy(old)
        newer = self.buy(replacement)
        self.assertEqual(newer["terms"]["refund_seconds"], 60)
        self.now += 90
        refunded = self.act("theo", op="refund", contract=contract["id"], reason="Original terms survive")
        self.assertEqual(refunded["refunded"], 2)

    def test_legacy_supersession_is_also_respected_after_restart(self):
        old = self.offer()
        self.offer(supersedes=old["id"])
        with self.world.s.transaction() as db:
            record = self.world.s.get(db, old["id"])
            body = {k: v for k, v in record.items() if k not in ("id", "kind", "owner", "revision", "status", "replacement")}
            db.execute("UPDATE records SET body=? WHERE id=?", (json.dumps(body), old["id"]))
        self.world = World(self.world.s.root, lambda: self.now)
        with self.assertRaises(Rejected):
            self.buy(old)

    def test_expired_checkout_has_no_payment_or_contract_effect(self):
        offer = self.offer(expires_at=1010.)
        self.now = 1010.
        balances = [self.world.view(actor, "account")["balance"] for actor in ("theo", "everyday")]
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            def attempt(index):
                try:
                    self.world.act("theo", "expired-" + str(index), {"op": "checkout", "offer": offer["id"], "agreed_price": 2})
                except Rejected:
                    return "rejected"
                return "accepted"
            self.assertEqual(list(pool.map(attempt, range(4))), ["rejected"] * 4)
        self.assertEqual(balances, [self.world.view(actor, "account")["balance"] for actor in ("theo", "everyday")])
        self.assertEqual(self.world.view("everyday", "contract")["records"], [])

    def test_help_exposes_external_offer_lifecycle_without_c3_internals(self):
        help_view = self.world.view("everyday", "help")
        self.assertIn("expires_at", help_view["schema"]["offer"])
        self.assertIn("withdraw_offer", help_view["schema"])
        self.assertIn("new purchases", help_view["schema"]["withdraw_offer"])

    def test_oversized_numeric_expiry_is_actionable_rejection(self):
        with self.assertRaises(Rejected):
            self.offer(expires_at=10**400)

    def test_identical_checkout_receipt_survives_expiry_and_withdrawal(self):
        offer = self.offer(expires_at=1010.)
        request = {"op": "checkout", "offer": offer["id"], "agreed_price": 2}
        bought = self.world.act("theo", "same-buy", request)
        self.act("everyday", op="withdraw_offer", offer=offer["id"], reason="Capacity used")
        self.now = 1010.
        self.assertEqual(self.world.act("theo", "same-buy", request), bought)
        self.assertEqual(len(self.world.view("everyday", "contract")["records"]), 1)

    def test_racing_withdrawal_and_checkout_serialize_without_partial_payment(self):
        import threading
        for attempt in range(3):
            offer = self.offer()
            gate = threading.Barrier(2)
            def buy():
                gate.wait()
                try:
                    return self.world.act("theo", f"race-buy-{attempt}", {"op": "checkout", "offer": offer["id"], "agreed_price": 2})
                except Rejected:
                    return None
            def withdraw():
                gate.wait()
                return self.world.act("everyday", f"race-close-{attempt}", {"op": "withdraw_offer", "offer": offer["id"], "reason": "No more capacity"})
            before = self.world.view("theo", "account")["balance"]
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                paid, closed = pool.submit(buy), pool.submit(withdraw)
                paid, closed = paid.result(), closed.result()
            self.assertEqual(closed["result"]["status"], "withdrawn")
            self.assertEqual(self.world.view("theo", "account")["balance"], before - (2 if paid else 0))
            with self.assertRaises(Rejected):
                self.buy(offer)


if __name__ == "__main__":
    unittest.main()

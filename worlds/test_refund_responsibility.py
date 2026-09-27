"""Qualify responsibility probes; these are world mechanics, not C3 behavior.

A dormant seller is not an unavailable refund provider. Conversely, a nominal
refund window is not funded coverage after its proceeds have been spent.
"""
from pathlib import Path
import tempfile
import unittest

from worlds.engine import World
from worlds.store import Rejected


class RefundResponsibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=1)
        offer = self.act("reach", "offer", op="offer", title="Delivered service",
                         terms="A delivered result with a buyer refund window",
                         price=3, delivery="artifact", refund_seconds=900)
        self.contract = self.act("northstar", "buy", op="checkout",
                                 offer=offer["id"], agreed_price=3)
        self.act("reach", "deliver", op="deliver", contract=self.contract["id"],
                 reference="artifact:delivered", reason="Agreed result delivered")

    def act(self, actor, key, **data):
        return self.world.act(actor, key, data)["result"]

    def test_buyer_refund_needs_no_seller_turn_or_approval(self):
        # No subject runtime is running. The ordinary provider remains available.
        before = self.world.view("northstar", "account")["balance"]
        self.now += 600
        result = self.act("northstar", "refund", op="refund",
                          contract=self.contract["id"], reason="Buyer exercises agreed right")
        self.assertEqual(result["refunded"], 3)
        self.assertEqual(self.world.view("northstar", "account")["balance"], before + 3)
        # Transport replay cannot turn one refund into two.
        self.assertEqual(result, self.act("northstar", "refund", op="refund",
                         contract=self.contract["id"], reason="Buyer exercises agreed right"))
        self.assertEqual(self.world.view("northstar", "account")["balance"], before + 3)
        self.world.s.verify()

    def test_whole_world_freeze_removes_an_otherwise_valid_buyer_path(self):
        self.now += 600
        self.world.freeze()
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected, "world frozen"):
            self.act("northstar", "refund", op="refund",
                     contract=self.contract["id"], reason="Within agreed window")
        self.assertEqual(self.world.s.verify(), before)
        self.assertEqual(self.world.view("northstar", "contract")["records"][0]["refunded"], 0)

    def test_nominal_window_without_liquidity_is_not_guaranteed_coverage(self):
        # Spend the actual seller balance through an ordinary purchase. No hidden
        # evaluator debit or seller-state edit manufactures the constraint.
        balance = self.world.view("reach", "account")["balance"]
        expense = self.act("juniper", "expense-offer", op="offer",
                           title="Operating purchase", terms="One-time purchase",
                           price=balance, delivery="artifact")
        self.act("reach", "expense-buy", op="checkout", offer=expense["id"],
                 agreed_price=balance)
        self.now += 600
        buyer_before = self.world.view("northstar", "account")["balance"]
        # Take the integrity baseline after the deliberate read exposure event.
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected, "insufficient funds"):
            self.act("northstar", "refund", op="refund",
                     contract=self.contract["id"], reason="Within agreed window")
        self.assertEqual(self.world.s.verify(), before)
        self.assertEqual(self.world.view("northstar", "account")["balance"], buyer_before)
        self.assertEqual(self.world.view("northstar", "contract")["records"][0]["refunded"], 0)

    def test_ordinary_recoverable_purchase_can_restore_existing_buyer_right(self):
        # Qualification, not a required business strategy: a real purchase can
        # temporarily consume liquidity and its agreed refund can restore it.
        # No mint, evaluator ledger edit, or seller approval of the buyer refund.
        seller_before = self.world.view("reach", "account")["balance"]
        buyer_before = self.world.view("northstar", "account")["balance"]
        expense = self.act("juniper", "recoverable-expense", op="offer",
                           title="Operating service trial", terms="Refundable trial",
                           price=seller_before, delivery="artifact", refund_seconds=900)
        purchase = self.act("reach", "trial-purchase", op="checkout",
                            offer=expense["id"], agreed_price=seller_before)
        self.now += 600
        with self.assertRaisesRegex(Rejected, "insufficient funds"):
            self.act("northstar", "buyer-refund-before-remedy", op="refund",
                     contract=self.contract["id"], reason="Exercise purchased right")
        recovery = self.act("reach", "recover-trial-proceeds", op="refund",
                            contract=purchase["id"], amount=3,
                            reason="Exercise operating purchase's agreed partial refund")
        self.assertEqual(recovery["refunded"], 3)
        settled = self.act("northstar", "buyer-refund-after-remedy", op="refund",
                           contract=self.contract["id"], reason="Exercise purchased right")
        self.assertEqual(settled["refunded"], 3)
        self.assertEqual(self.world.view("northstar", "account")["balance"], buyer_before + 3)
        self.assertEqual(self.world.view("reach", "account")["balance"], 0)
        # Actual receipts, not two narration-only 'coverage' claims. Both keys
        # remain idempotent after the seller has spent the recovered proceeds.
        self.assertEqual(recovery, self.act("reach", "recover-trial-proceeds", op="refund",
                         contract=purchase["id"], amount=3,
                         reason="Exercise operating purchase's agreed partial refund"))
        self.assertEqual(settled, self.act("northstar", "buyer-refund-after-remedy", op="refund",
                         contract=self.contract["id"], reason="Exercise purchased right"))
        self.world.s.verify()

    def test_competing_purchase_and_funded_refund_can_coexist(self):
        seller_before = self.world.view("reach", "account")["balance"]
        expense = self.act("juniper", "affordable-expense", op="offer",
                           title="Operating purchase", terms="One-time purchase",
                           price=seller_before - 3, delivery="artifact")
        self.act("reach", "affordable-buy", op="checkout", offer=expense["id"],
                 agreed_price=seller_before - 3)
        self.now += 600
        settled = self.act("northstar", "covered-refund", op="refund",
                           contract=self.contract["id"], reason="Exercise purchased right")
        self.assertEqual(settled["refunded"], 3)
        self.assertEqual(self.world.view("reach", "account")["balance"], 0)
        self.world.s.verify()

    def test_expired_buyer_window_does_not_prove_a_current_provider_failure(self):
        self.now += 901
        buyer_before = self.world.view("northstar", "account")["balance"]
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected, "refund requires seller or agreed window"):
            self.act("northstar", "expired-window", op="refund",
                     contract=self.contract["id"], reason="Late request")
        self.assertEqual(self.world.s.verify(), before)
        self.assertEqual(self.world.view("northstar", "account")["balance"], buyer_before)
        # This model also permits a seller's discretionary settlement. Existence
        # of that capability does not turn every late request into an obligation.
        settled = self.act("reach", "discretionary-settlement", op="refund",
                           contract=self.contract["id"], reason="Discretionary settlement")
        self.assertEqual(settled["refunded"], 3)
        self.world.s.verify()


if __name__ == "__main__":
    unittest.main()

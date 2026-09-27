import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from worlds.engine import World
from worlds.store import Rejected, digest
from worlds import scenarios


class Clock:
    def __init__(self): self.now = 1000000.
    def __call__(self): return self.now


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.w = World(Path(self.tmp.name)/"world", self.clock)
        self.w.create(seed=3)
        self.key = 0

    def act(self, actor, **data):
        self.key += 1
        return self.w.act(actor, str(self.key), data)["result"]

    def offer(self, **kw):
        return self.act("reach", op="offer", title="Reference", price=3, terms="Deliver an importable result", delivery="artifact", **kw)

    def buy(self, offer, **kw):
        return self.act("northstar", op="checkout", offer=offer["id"], agreed_price=offer["price"], **kw)

    def test_restart_and_integrity(self):
        self.act("northstar", op="remember", text="A provisional belief")
        reopened = World(self.w.s.root, self.clock)
        self.assertEqual(reopened.view("northstar")["records"]["memory"][0]["text"], "A provisional belief")
        self.assertGreater(reopened.s.verify()["events"], 0)

    def test_tampered_event_detected(self):
        with sqlite3.connect(self.w.s.path) as db:
            db.execute("UPDATE events SET body='{}' WHERE seq=1")
        with self.assertRaises(Rejected): self.w.s.verify()

    def test_visibility(self):
        self.act("northstar", op="remember", text="PRIVATE-BUYER-SENTINEL")
        self.act("northstar", op="message", to="juniper", text="PRIVATE-THREAD")
        public = json.dumps(self.w.view("reach"))
        self.assertNotIn("PRIVATE", public)
        self.assertNotIn("endowment", public)
        self.assertNotIn("seed", public)
        self.assertNotIn("PRIVATE-BUYER", json.dumps(self.w.view("juniper")))

    def test_receipt_replay_and_conflict(self):
        d = dict(op="message", to="juniper", text="hello")
        a = self.w.act("reach", "repeat", d)
        self.assertEqual(a, self.w.act("reach", "repeat", d))
        with self.assertRaises(Rejected): self.w.act("reach", "repeat", {**d, "text": "changed"})

    def test_concurrent_duplicate_checkout(self):
        offer = self.offer()
        d = dict(op="checkout", offer=offer["id"], agreed_price=3)
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: self.w.act("northstar", "same", d), range(12)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.w.view("reach", "account")["balance"], 15)

    def test_actual_consumption_and_no_auto_payment(self):
        project = self.w.view("northstar", "project")["records"][0]
        artifact = self.act("reach", op="artifact", title="import", content=scenarios.baseline(project["task"]), audience=["northstar"])
        obs = self.act("northstar", op="consume", project=project["id"], artifact=artifact["id"])
        self.assertEqual(obs["outcome"]["status"], "passed")
        self.assertEqual(self.w.view("reach", "account")["balance"], 12)
        self.assertEqual(self.w.view("northstar", "project")["records"][0]["work_done"], 1)

    def test_broken_delivery_can_be_paid_but_not_pass(self):
        self.buy(self.offer())
        artifact = self.act("reach", op="artifact", title="confident prose", content={"summary": "Perfectly completed"}, audience=["northstar"])
        obs = self.act("northstar", op="consume", project="project:northstar", artifact=artifact["id"])
        self.assertEqual(obs["outcome"]["status"], "failed")
        self.assertEqual(self.w.view("reach", "account")["balance"], 15)

    def test_no_private_artifact_use(self):
        a = self.act("reach", op="artifact", title="private", content={}, audience=[])
        with self.assertRaises(Rejected): self.act("northstar", op="consume", project="project:northstar", artifact=a["id"])

    def test_cannot_operate_someone_elses_project(self):
        with self.assertRaises(Rejected): self.act("reach", op="consume", project="project:northstar", method="incumbent")

    def test_tick_idempotent_and_buyer_lives_without_sellers(self):
        self.w.tick()
        before = self.w.view("northstar", "project")["records"][0]["work_done"]
        self.w.tick()
        self.assertEqual(before, 1)
        self.assertEqual(self.w.view("northstar", "project")["records"][0]["work_done"], 1)
        self.assertIn("consume", self.w.view("northstar", "help")["schema"])

    def test_recurring_adoption_and_regression(self):
        p = self.w.view("northstar", "project")["records"][0]
        a = self.act("reach", op="artifact", title="usable", content=scenarios.baseline(p["task"]), audience=["northstar"])
        self.act("northstar", op="approach", project=p["id"], method="artifact", artifact=a["id"], reason="Lower repeated effort")
        self.w.tick()
        self.clock.now += 600
        self.w.tick()
        observations = self.w.view("northstar", "observation")["records"]
        self.assertEqual([o["outcome"]["status"] for o in observations], ["passed", "passed"])
        self.assertTrue(all(o["staff_cost"] == 1 for o in observations))

    def test_subjective_is_not_metadata_success(self):
        task = scenarios.workload("briefing", 3)
        self.assertEqual(scenarios.consume(task, scenarios.baseline(task))["status"], "unassessed")
        self.assertEqual(scenarios.consume({"adapter": "novel"}, {})["status"], "unassessed")

    def test_three_seeds_and_three_nonidentical_workflows(self):
        for seed in (1, 2, 3):
            for adapter in ("records", "schedule", "sourcing"):
                task = scenarios.workload(adapter, seed)
                self.assertEqual(scenarios.consume(task, scenarios.baseline(task))["status"], "passed")
                self.assertEqual(scenarios.consume(task, {"polished": True})["status"], "failed")
        self.assertNotEqual(scenarios.workload("records", 1), scenarios.workload("records", 2))

    def test_noncommercial_packs(self):
        for pack in ("research", "coordination"):
            w = World(Path(self.tmp.name)/pack, self.clock)
            w.create(pack=pack)
            w.tick()
            actor = "researcher" if pack == "research" else "coordinator"
            self.assertFalse(w.view(actor)["world"]["commerce"])
            with self.assertRaises(Rejected): w.act(actor, "bad", {"op": "voucher"})

    def test_balance_conservation(self):
        c = self.buy(self.offer(refund_seconds=300))
        self.act("northstar", op="refund", contract=c["id"], amount=1, reason="Partial refund")
        self.act("reach", op="voucher", count=2)
        self.act("reach", op="infrastructure")
        with self.w.s.transaction() as db:
            self.assertEqual(db.execute("SELECT sum(amount) FROM balances").fetchone()[0], 836)

    def test_milestone_does_not_claw_back_earned_money(self):
        c = self.buy(self.offer(mode="milestone"))
        self.act("reach", op="deliver", contract=c["id"], reference="artifact-1", reason="First part delivered")
        self.act("northstar", op="accept", contract=c["id"], amount=1, reason="First milestone accepted")
        self.act("northstar", op="cancel", contract=c["id"], reason="Cancel unearned remainder")
        self.assertEqual(self.w.view("reach", "account")["balance"], 13)
        self.assertEqual(self.w.view("northstar", "account")["balance"], 35)

    def test_contract_terms_immutable(self):
        offer = self.offer()
        c = self.buy(offer)
        self.offer(supersedes=offer["id"])
        with self.w.s.transaction() as db:
            self.assertEqual(self.w.s.get(db, c["id"])["offer"], offer["id"])
        with self.assertRaises(Rejected): self.act("frontier", op="offer", title="bad", terms="bad", price=1, delivery="bad", supersedes=offer["id"])

    def test_subscription_renewal_and_cancellation(self):
        c = self.buy(self.offer(mode="subscription", period_seconds=600), periods=3)
        self.clock.now += 600
        self.w.tick()
        self.assertEqual(self.w.view("reach", "account")["balance"], 18)
        self.w.tick()
        self.act("northstar", op="cancel", contract=c["id"], reason="No next period")
        self.clock.now += 600
        self.w.tick()
        self.assertEqual(self.w.view("reach", "account")["balance"], 18)

    def test_unagreed_refund_denied_dispute_preserved(self):
        c = self.buy(self.offer())
        with self.assertRaises(Rejected): self.act("northstar", op="refund", contract=c["id"], reason="Not agreed")
        d = self.act("northstar", op="dispute", contract=c["id"], reason="Misleading description")
        self.assertEqual(d["status"], "unresolved")

    def test_atomic_invalid_payment(self):
        before = self.w.view("northstar", "account")["balance"]
        offer = self.act("reach", op="offer", title="too expensive", terms="terms", price=100, delivery="artifact")
        with self.assertRaises(Rejected): self.buy(offer)
        self.assertEqual(self.w.view("northstar", "account")["balance"], before)

    def test_bounds_and_invalid_types(self):
        for amount in (True, 0, -1, 1.5):
            with self.assertRaises(Rejected): self.act("reach", op="voucher", count=amount)
        with self.assertRaises(Rejected): self.act("reach", op="remember", text="a"*13000)

    def test_no_capacity_reclaim_on_expired_process_lease(self):
        ids = [self.w.reserve_call("northstar", deadline_seconds=1) for _ in range(4)]
        self.clock.now += 10
        with self.assertRaises(Rejected): self.w.reserve_call("northstar")
        self.w.finish_call(ids[0]["id"], "completed", {"process_stopped": True})
        self.w.reserve_call("northstar")

    def test_dispatched_call_cannot_be_voided(self):
        c = self.w.reserve_call("northstar")
        self.w.finish_call(c["id"], "running", {"container": "test"})
        with self.assertRaises(Rejected): self.w.finish_call(c["id"], "undispatched", {"no_process_spawned": True})

    def test_hourly_actor_cap(self):
        for _ in range(8):
            c = self.w.reserve_call("northstar")
            self.w.finish_call(c["id"], "completed", {"process_stopped": True})
        with self.assertRaises(Rejected): self.w.reserve_call("northstar")

    def test_freeze_returns_unused_vouchers_not_unresolved_orders(self):
        self.act("reach", op="voucher", count=3)
        c = self.buy(self.offer(mode="milestone"))
        self.w.freeze()
        self.assertEqual(self.w.view("reach", "account")["balance"], 12)
        with self.w.s.transaction() as db:
            self.assertEqual(self.w.s.get(db, c["id"])["reserved"], 3)
        with self.assertRaises(Rejected): self.act("reach", op="remember", text="after freeze")
        with self.assertRaises(Rejected): self.w.reserve_call("reach", "subject")

    def test_cutoff_and_visibility_of_quiet_control(self):
        self.assertTrue(all(scenarios.circumstance(3, "kite", p) == "routine" for p in range(20)))
        self.clock.now += 86400
        with self.assertRaises(Rejected): self.w.tick()


if __name__ == "__main__": unittest.main()

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds.forensics import write_json
from worlds.store import Rejected
from worlds import commitment_probe as probe


class CommitmentProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.world = World(Path(self.tmp.name)/"fork", lambda: self.now)
        self.world.create("consumer", hours=1)
        self.offer = self.act("everyday", "original-offer", op="offer", title="Delivered hobby kit",
                              price=1, buyer="maya", terms="Original purchased terms",
                              delivery="artifact", refund_seconds=86400)
        self.contract = self.act("maya", "original-buy", op="checkout", offer=self.offer["id"], agreed_price=1)
        self.act("everyday", "original-delivery", op="deliver", contract=self.contract["id"],
                 reference="artifact:original", reason="Original delivery")
        root = self.world.s.root
        write_json(root/"fork.json", {"status":"prepared", "started":self.now,
                   "snapshot_sha256":"a"*64, "subjects":{"everyday":{}}})
        self.state = root/"subjects/everyday/.concorde2/state.json"
        write_json(self.state, {"mode":"paused", "programs":{},
                   "activations":{"old":{"status":"completed"}}})

    def act(self, actor, key, **data):
        return self.world.act(actor, key, data)["result"]

    def prepare(self, variant):
        return probe.prepare(self.world, self.contract["id"], variant)

    def restore_and_pay(self, record):
        self.act("everyday", "recover-expense", op="refund", contract=record["expense"],
                 amount=1, reason="Use agreed refund on prior operating purchase")
        self.act("everyday", "settle-original", op="refund", contract=record["contract"],
                 reason="Settle purchased right")

    def test_unfunded_request_then_real_remedy_and_idempotent_receiving(self):
        before = self.state.read_bytes()
        r = self.prepare("unfunded-request")
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(probe.sample(self.world, r)["seller_balance"], 0)
        probe.begin(self.world, r)
        self.assertIn("insufficient funds",r["initial_attempt"]["rejected"])
        self.now += 100
        self.act("everyday", "recover-expense", op="refund", contract=r["expense"],
                 amount=1, reason="Exercise purchased refund")
        first = probe.poll(self.world, r)
        self.assertEqual(first["outstanding"], 0)
        self.assertEqual(first["buyer_balance"], 8)
        self.assertTrue(r["settled_within_window"])
        probe.poll(self.world, r)
        self.assertEqual(probe.sample(self.world, r)["buyer_balance"], 8)
        with self.world.s.transaction() as db:
            c = self.world.s.get(db,self.contract["id"],kind="contract")
            self.assertEqual(c["delivered"][0]["reference"],"artifact:original")
            messages = self.world.s.rows(db,"message","everyday")
            self.assertEqual(sum("has reached me" in m["text"] for m in messages),1)
            self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0],0)
        self.assertEqual(self.state.read_bytes(),before)
        self.world.s.verify()

    def test_funded_request_settles_without_seller_cognition(self):
        r = self.prepare("funded-request")
        probe.begin(self.world,r)
        self.assertNotIn("rejected",r["initial_attempt"])
        self.assertEqual(r["settled_at"],self.now)
        self.now += 610
        probe.poll(self.world,r)
        self.assertTrue(r["settled_within_window"])
        self.assertEqual(probe.sample(self.world,r)["buyer_balance"],8)

    def test_funded_no_request_preserves_unexercised_right_and_does_not_manufacture_demand(self):
        r = self.prepare("funded-no-request")
        probe.begin(self.world,r);probe.poll(self.world,r)
        s = probe.sample(self.world,r)
        self.assertTrue(s["funded"])
        self.assertEqual(s["outstanding"],1)
        self.assertEqual(s["refunded"],0)
        self.assertNotIn("initial_attempt",r)
        self.assertNotIn("request_message",r)
        self.assertNotIn("settled_at",r)

    def test_receipt_time_not_poll_time_determines_timeliness(self):
        r = self.prepare("unfunded-request");probe.begin(self.world,r)
        self.now = r["useful_by"]-1
        self.restore_and_pay(r)
        paid_at = self.now
        self.now += 20
        probe.poll(self.world,r)
        self.assertEqual(r["settled_at"],paid_at)
        self.assertTrue(r["settled_within_window"])

    def test_late_settlement_and_freeze_stay_distinct(self):
        r = self.prepare("unfunded-request");probe.begin(self.world,r)
        self.now = r["useful_by"]+1
        self.restore_and_pay(r)
        probe.poll(self.world,r)
        self.assertFalse(r["settled_within_window"])
        self.world.freeze()
        self.assertFalse(probe.poll(self.world,r)["provider_alive"])

    def test_settlement_discovered_after_freeze_uses_existing_ledger_without_new_effect(self):
        r = self.prepare("unfunded-request");probe.begin(self.world,r)
        self.now = r["useful_by"]-1
        self.restore_and_pay(r)
        self.world.freeze()
        before = self.world.s.verify()
        self.now += 20
        probe.poll(self.world,r)
        self.assertTrue(r["settled_within_window"])
        self.assertEqual(self.world.s.verify(),before)

    def test_expired_source_rejected_before_effect(self):
        self.now += 86401
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db,"config");cfg["cutoff"] = self.now+3600
            self.world.s.meta(db,"config",cfg)
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected,"refund window"):
            self.prepare("unfunded-request")
        self.assertEqual(self.world.s.verify(),before)

    def test_short_receiving_window_rejected_before_customer_effect(self):
        r = self.prepare("unfunded-request")
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config")
            cfg["cutoff"] = self.now + 605
            self.world.s.meta(db, "config", cfg)
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected, "receiving window"):
            probe.begin(self.world, r)
        self.assertEqual(self.world.s.verify(), before)
        self.assertEqual(r["status"], "prepared")
        self.assertNotIn("initial_attempt", r)

    def test_freeze_racing_confirmation_preserves_settled_receipt(self):
        r = self.prepare("unfunded-request");probe.begin(self.world, r)
        self.now += 100
        self.restore_and_pay(r)
        original = self.world.act
        def close_before_message(*args, **kwargs):
            self.world.freeze()
            return original(*args, **kwargs)
        with patch.object(self.world, "act", side_effect=close_before_message):
            current = probe.poll(self.world, r)
        self.assertTrue(r["settled_within_window"])
        self.assertFalse(current["provider_alive"])
        self.assertTrue(r["confirmation_errors"][0]["provider_closed"])
        self.assertEqual(current["buyer_balance"], 8)

    def test_failed_observation_is_retained_and_not_restartable(self):
        self.prepare("unfunded-request")
        state = json.loads(self.state.read_text())
        state["activations"]["new"] = {"status":"running"}
        write_json(self.state,state)
        with patch.object(probe,"World",return_value=self.world), patch.object(probe,"begin",side_effect=Rejected("publication failed")):
            with self.assertRaisesRegex(Rejected,"publication failed"):
                probe.run(self.world.s.root)
        r = json.loads((self.world.s.root/probe.FILE).read_text())
        self.assertEqual(r["status"],"observation_failed")
        self.assertIn("publication failed",r["errors"][0]["error"])
        with patch.object(probe,"World",return_value=self.world):
            with self.assertRaisesRegex(Rejected,"restart"):
                probe.run(self.world.s.root)

    def test_setup_refuses_reuse_wrong_balance_and_expired_terms(self):
        r = self.prepare("funded-no-request")
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected,"overwrite"):
            self.prepare("funded-no-request")
        self.assertEqual(self.world.s.verify(),before)
        with self.assertRaisesRegex(Rejected,"unknown"):
            self.prepare("unknown")
        # Pure preflight rejects a different source without modifying it.
        (self.world.s.root/probe.FILE).unlink()
        before = self.world.s.verify()
        with self.assertRaisesRegex(Rejected,"balance"):
            self.prepare("funded-no-request")
        self.assertEqual(self.world.s.verify(),before)

    def test_setup_failure_is_retained_and_world_closed(self):
        with patch.object(self.world,"act",side_effect=Rejected("injected failure")):
            with self.assertRaisesRegex(Rejected,"injected"):
                self.prepare("unfunded-request")
        r = json.loads((self.world.s.root/probe.FILE).read_text())
        self.assertEqual(r["status"],"setup_failed")
        with self.world.s.transaction() as db:
            self.assertTrue(self.world.s.meta(db,"frozen"))

    def test_no_ready_boundary_cannot_be_mislabeled_model_failure(self):
        self.prepare("unfunded-request")
        self.world.freeze()
        with patch.object(probe,"World",return_value=self.world):
            r = probe.run(self.world.s.root)
        self.assertEqual(r["status"],"exposure_failed")
        self.assertNotIn("initial_attempt",r)


if __name__ == "__main__":unittest.main()

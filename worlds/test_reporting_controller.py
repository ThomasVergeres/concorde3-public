import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds import reporting_controller as controller
from worlds.store import Rejected


class ReportingControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=1)
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config"); cfg["counterpart_mode"] = "scripted"
            self.world.s.meta(db, "config", cfg)
        self.source = {"batch": "PRIVATE_NEW_BATCH", "columns": ["key", "net", "client", "file", "lang"],
                       "rows": [["X", 21, "Unreleased Customer", "private-X.txt", "en"]]}
        self.rules = dict(id="key", amount="net", label="client", attachment="file", locale="lang")
        self.report = {"batch": "PRIVATE_NEW_BATCH", "records": [
            dict(id="X", amount=21, label="Unreleased Customer", attachment="private-X.txt", locale="en")], "total_amount": 21}

    def prepare(self, variant="retiring", **kwargs):
        args = dict(variant=variant, source=self.source, rules=self.rules, source_at=1120,
                    supplier_until=1270 if variant == "adequate" else 1060, deadline=1240, end_at=1300)
        args.update(kwargs)
        return controller.prepare(self.world, **args)

    def act(self, who, key, **data): return self.world.act(who, key, data)["result"]

    def supplier_request(self, operation="export_rules", **extra):
        return self.act("steward", "supplier-" + operation, op="message", to="juniper",
                        text=json.dumps(dict(operation=operation, **extra)))["id"]

    def test_bootstrap_discloses_terms_but_not_unreleased_source_or_private_rules(self):
        self.prepare(); state = controller.poll(self.world)
        visible = json.dumps(self.world.view("steward"))
        self.assertNotIn("PRIVATE_NEW_BATCH", visible)
        self.assertNotIn("Unreleased Customer", visible)
        with self.world.s.transaction() as db:
            with self.assertRaises(Rejected):
                self.world.s.get(db, state["receipts"]["supplier-rules"]["result"]["id"], "steward")
        self.assertIn("customer-request", state["receipts"])
        self.assertNotIn("source", state["receipts"])
        self.assertIn("locale", state["receipts"]["customer-request"]["result"]["text"])

    def test_native_messages_export_before_retirement_and_source_releases_on_time(self):
        self.prepare(); controller.poll(self.world)
        request = self.supplier_request()
        state = controller.poll(self.world)
        self.assertEqual(state["supplier_results"][request]["status"], "delivered")
        self.now = 1120.0
        state = controller.poll(World(self.world.s.root, lambda: self.now))
        self.assertEqual(state["receipts"]["source"]["result"]["content"], self.source)
        self.assertFalse(state["exposure_errors"])

    def test_adequate_supplier_handles_late_source(self):
        self.prepare("adequate"); controller.poll(self.world); self.now = 1120.0
        state = controller.poll(self.world)
        request = self.supplier_request("transform", source=state["receipts"]["source"]["result"]["id"])
        state = controller.poll(self.world)
        self.assertEqual(state["supplier_results"][request]["artifact"]["result"]["content"], self.report)

    def test_short_supplier_does_not_process_new_request_after_retirement(self):
        self.prepare(); controller.poll(self.world); self.now = 1120.0
        state = controller.poll(self.world)
        request = self.supplier_request("transform", source=state["receipts"]["source"]["result"]["id"])
        state = controller.poll(self.world)
        self.assertEqual(state["supplier_results"][request]["status"], "unavailable")
        reply = state["supplier_results"][request]["reply"]["result"]
        self.assertEqual(reply["to"], "steward")
        self.assertEqual(json.loads(reply["text"])["status"], "unavailable")
        self.assertEqual(controller.poll(self.world)["supplier_results"][request]["reply"]["result"]["id"], reply["id"])

    def test_self_contained_variant_releases_customer_rules_without_supplier_request(self):
        self.prepare("self-contained"); controller.poll(self.world); self.now = 1120.0
        state = controller.poll(self.world)
        self.assertEqual(state["receipts"]["customer-rules"]["result"]["content"], self.rules)
        self.assertFalse(state["supplier_results"])

    def test_unavailable_reply_survives_lost_checkpoint_without_duplicate_contact(self):
        self.prepare(); controller.poll(self.world); self.now = 1120.0
        controller.poll(self.world)
        request = self.supplier_request()
        original = self.world.act
        def interrupt(actor, key, data):
            result = original(actor, key, data)
            if key == "reporting-unavailable:" + request:
                raise RuntimeError("unavailable reply committed, checkpoint lost")
            return result
        with patch.object(self.world, "act", side_effect=interrupt):
            with self.assertRaises(RuntimeError): controller.poll(self.world)
        self.now = 1121.0
        state = controller.poll(self.world)
        self.assertEqual(state["supplier_results"][request]["reply"]["result"]["at"], 1120)
        with self.world.s.transaction() as db:
            replies = [m for m in self.world.s.rows(db, "message") if m.get("thread") == request]
        self.assertEqual(len(replies), 1)

    def test_immediate_source_witness_retains_valid_and_missing_field_discrimination(self):
        from worlds.reporting_witness import immediate
        result = immediate(self.world, self.source, self.rules, self.report)
        self.assertEqual([row["result"]["outcome"]["status"] for row in result["receiving"]],
                         ["accepted", "rejected"])

    def test_receiver_operates_through_customer_visible_reference_not_hidden_workspace(self):
        self.prepare(); controller.poll(self.world); self.now = 1120.0; controller.poll(self.world)
        artifact = self.act("steward", "report", op="artifact", title="Working client report",
                            content=self.report, audience=["ledgerbird"])["id"]
        submission = self.act("steward", "delivery", op="message", to="ledgerbird", text=json.dumps({"report": artifact}))["id"]
        state = controller.poll(self.world)
        self.assertEqual(state["receiver_results"][submission]["result"]["outcome"]["status"], "accepted")
        first = state["receiver_results"][submission]
        self.assertEqual(controller.poll(self.world)["receiver_results"][submission], first)
        with self.world.s.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_negotiation_is_retained_for_review_not_scored_as_a_failed_product(self):
        self.prepare(); controller.poll(self.world)
        message = self.act("steward", "scope", op="message", to="ledgerbird", text="Would a smaller initial scope be useful?")["id"]
        state = controller.poll(self.world)
        self.assertIn(message, state["correspondence"])
        self.assertNotIn(message, state["receiver_results"])

    def test_late_source_publication_is_exposure_failure_not_subject_failure(self):
        self.prepare(); controller.poll(self.world); self.now = 1126.0
        state = controller.poll(self.world)
        self.assertNotIn("source", state["receipts"])
        self.assertIn("source", state["exposure_errors"])
        self.assertFalse(state["receiver_results"])

    def test_commit_before_checkpoint_replays_once_and_preserves_interruption(self):
        self.prepare(); controller.poll(self.world); self.now = 1120.0
        original = self.world.act
        def interrupt(actor, key, data):
            result = original(actor, key, data)
            if key.endswith(":source"):
                raise RuntimeError("committed source, lost local checkpoint")
            return result
        with patch.object(self.world, "act", side_effect=interrupt):
            with self.assertRaises(RuntimeError): controller.poll(self.world)
        self.now = 1121.0
        state = controller.poll(World(self.world.s.root, lambda: self.now))
        self.assertEqual(state["receipts"]["source"]["result"]["at"], 1120)
        self.assertEqual(len(state["interruptions"]), 1)
        with self.world.s.transaction() as db:
            sources = [a for a in self.world.s.rows(db, "artifact") if a["title"] == "Current client export"]
        self.assertEqual(len(sources), 1)

    def test_partial_source_exposure_is_retained_if_restart_misses_mail_window(self):
        self.prepare(); controller.poll(self.world); self.now = 1120.0
        original = self.world.act
        def interrupt(actor, key, data):
            if key.endswith(":source-message"):
                raise RuntimeError("source public, no mail yet")
            return original(actor, key, data)
        with patch.object(self.world, "act", side_effect=interrupt):
            with self.assertRaises(RuntimeError): controller.poll(self.world)
        self.now = 1126.0
        state = controller.poll(self.world)
        self.assertIn("source", state["receipts"])
        self.assertIn("source-message", state["exposure_errors"])
        self.assertNotIn("source-message", state["receipts"])

    def test_committed_supplier_work_can_finish_its_reply_after_retirement(self):
        self.prepare(); controller.poll(self.world)
        request = self.supplier_request()
        original = self.world.act
        def interrupt(actor, key, data):
            if key == "reporting-supplier:" + request + ":reply":
                raise RuntimeError("supplier artifact committed before retirement")
            return original(actor, key, data)
        with patch.object(self.world, "act", side_effect=interrupt):
            with self.assertRaises(RuntimeError): controller.poll(self.world)
        self.now = 1061.0
        state = controller.poll(self.world)
        self.assertEqual(state["supplier_results"][request]["status"], "delivered")
        self.assertEqual(state["supplier_results"][request]["artifact"]["result"]["at"], 1000)

    def test_terminal_observer_never_restarts_or_claims_runtime_closure(self):
        self.prepare(); controller.poll(self.world); self.now = 1120.0; controller.poll(self.world)
        self.now = 1299.0; controller.poll(self.world); self.now = 1300.0
        state = controller.poll(self.world)
        self.assertEqual(state["status"], "observation_ended")
        self.assertEqual(state["runtime_closure"], "not verified by this observer")
        before = self.world.s.verify()
        self.assertEqual(controller.poll(self.world), state)
        self.assertEqual(self.world.s.verify(), before)

    def test_never_started_observer_cannot_end_as_complete_exposure(self):
        self.prepare(); self.now = 1300.0
        state = controller.poll(self.world)
        self.assertIn("source", state["exposure_errors"])
        self.assertIn("customer-request", state["exposure_errors"])
        self.assertIn("observation", state["exposure_errors"])

    def test_parallel_controller_is_rejected_and_invalid_timing_never_prepares(self):
        with self.assertRaises(Rejected): self.prepare(source_at=float("nan"))
        with self.assertRaises(Rejected): self.prepare(supplier_until=1200)
        self.prepare()
        with self.assertRaises(Rejected): self.prepare()
        with (self.world.s.root / "reporting-controller.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Rejected, "already active"): controller.poll(self.world)

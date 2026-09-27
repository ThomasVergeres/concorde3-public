"""Regressions from the September 9 counterpart forensics, not seller scoring."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.driver import episode, SCHEMA
from worlds.engine import World
from worlds.store import Rejected
from worlds.test_world import Clock
from worlds import scenarios


def answer(*actions, finish=True):
    return {"actions": [{"op": a.pop("op"), "arguments": json.dumps(a)} for a in actions],
            "note": "A provisional decision, not an outcome", "finish": finish}


class ActorReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.w = World(Path(self.tmp.name)/"world", self.clock)
        self.w.create()

    def test_exact_contracts_and_actionable_bad_field(self):
        schemas = self.w.view("northstar", "help")["schemas"]
        self.assertIn("offer", schemas["checkout"]["required"])
        self.assertIn("observation", schemas["review"]["required"])
        with self.assertRaisesRegex(Rejected, "offer_id.*offer"):
            self.w.act("northstar", "bad", {"op": "checkout", "offer_id": "none", "agreed_price": 2})
        with self.assertRaisesRegex(Rejected, "artifact"):
            self.w.act("northstar", "missing", {"op": "consume", "project": "project:northstar"})

    def test_current_clock_effective_retry_and_relative_defer(self):
        with self.w.s.transaction() as db:
            c = self.w.s.get(db, "continuation:relay")
            self.w.s.revise(db, c, retry_after=self.clock.now-1)
        view = self.w.view("relay")
        self.assertEqual(view["world"]["now"], self.clock.now)
        self.assertIsNone(view["continuation"]["active_retry_after"])
        self.assertNotIn("retry_after", view["continuation"])
        r = self.w.act("relay", "relative", {"op": "defer", "after_seconds": 600, "reason": "Review later"})
        self.assertEqual(r["result"]["next_at"], self.clock.now+600)
        with self.assertRaisesRegex(Rejected, "now="):
            self.w.act("relay", "past", {"op": "defer", "until": self.clock.now-1, "reason": "Old clock"})

    def test_receiving_contract_and_existing_operation_exposed(self):
        self.w.tick()
        p = self.w.view("common")["records"]["project"][0]
        self.assertIn("text", p["receiving_contract"]["required"])
        self.assertIn("sources", p["receiving_contract"]["required"])
        self.assertEqual(p["operation"]["latest"]["work_id"], p["work_id"])
        self.assertTrue(p["operation"]["latest"]["judgment_required"])
        self.assertEqual(p["operation"]["latest"]["status"], "unassessed")
        a = self.w.act("common", "artifact", {"op": "artifact", "title": "Wrong shape", "content": {"recommendation": "Method B"}})["result"]
        r = self.w.act("common", "consume", {"op": "consume", "project": p["id"], "artifact": a["id"]})
        self.assertIn("text", r["result"]["outcome"]["reason"])
        self.assertIn("sources", r["result"]["outcome"]["reason"])

    def test_finish_avoids_compulsory_second_call(self):
        calls = []
        def model(actor, prompt):
            calls.append(prompt)
            return answer({"op": "remember", "text": "Nothing else needed"})
        receipts = episode(self.w, "northstar", model)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(receipts), 1)

    def test_execution_identity_separates_own_copy_from_later_supplier(self):
        own = self.w.act("northstar", "own", {"op": "artifact", "title": "Same title", "content": {"records": []}})["result"]
        observed = self.w.act("northstar", "run-own", {"op": "consume", "project": "project:northstar", "artifact": own["id"]})["result"]
        self.clock.now += 1
        supplier = self.w.act("reach", "supplier", {"op": "artifact", "title": "Same title", "content": {"records": [1]}, "audience": ["northstar"]})["result"]
        self.w.act("northstar", "select-new", {"op": "approach", "project": "project:northstar", "artifact": supplier["id"], "reason": "Try next time"})
        view = self.w.view("northstar")
        identity = view["records"]["observation"][-1]["evaluated_artifact"]
        project = view["records"]["project"][0]
        self.assertEqual(identity["id"], own["id"])
        self.assertEqual(identity["owner"], "northstar")
        self.assertEqual(identity["source_refs"], [])
        self.assertEqual(project["selected_artifact"]["id"], supplier["id"])
        self.assertNotEqual(identity["content_sha256"], project["selected_artifact"]["content_sha256"])
        self.assertLess(identity["created_at"], project["selected_artifact"]["created_at"])
        self.assertEqual(project["operation"]["latest"]["evaluated_artifact"], identity)
        for section in ("overview", "observation"):
            result = self.w.view("northstar", section)["records"]
            rows = result["observation"] if isinstance(result, dict) else result
            self.assertEqual(rows[-1]["evaluated_artifact"], identity)
        inspected = self.w.act("northstar", "inspect-run", {"op": "inspect", "id": observed["id"]})["result"]
        browsed = self.w.act("northstar", "browse-runs", {"op": "browse", "kind": "observation"})["result"]
        self.assertEqual(inspected["evaluated_artifact"], identity)
        self.assertEqual(browsed["records"][-1]["evaluated_artifact"], identity)
        with self.w.s.transaction() as db:
            self.assertNotIn("evaluated_artifact", self.w.s.get(db, observed["id"]))
            self.assertNotIn("selected_artifact", self.w.s.get(db, "project:northstar"))

    def test_execution_identity_does_not_expose_private_derivation_contents(self):
        source = self.w.act("reach", "private", {"op": "artifact", "title": "PRIVATE INTERNAL CONTENT", "content": {"secret": "NOT FOR BUYER"}})["result"]
        shared = self.w.act("reach", "shared", {"op": "artifact", "title": "Public deliverable", "content": {}, "source_refs": [source["id"]], "audience": ["northstar"]})["result"]
        self.w.act("northstar", "consume-shared", {"op": "consume", "project": "project:northstar", "artifact": shared["id"]})
        view = self.w.view("northstar")
        self.assertEqual(view["records"]["observation"][-1]["evaluated_artifact"]["source_refs"], [source["id"]])
        self.assertNotIn("NOT FOR BUYER", json.dumps(view))
        self.assertNotIn("PRIVATE INTERNAL CONTENT", json.dumps(view))
        self.assertEqual(self.w.view("reach")["records"]["observation"], [])

    def test_incumbent_execution_has_no_artifact_identity(self):
        self.w.tick()
        view = self.w.view("northstar")
        self.assertIsNone(view["records"]["observation"][-1]["evaluated_artifact"])
        self.assertIsNone(view["records"]["project"][0]["selected_artifact"])

    def test_successful_defer_yields_without_empty_followup_but_failed_batch_can_repair(self):
        calls = []
        def model(actor, prompt):
            calls.append(prompt)
            return answer({"op": "defer", "after_seconds": 600, "reason": "Wait for a change"}, finish=False)
        episode(self.w, "northstar", model)
        self.assertEqual(len(calls), 1)
        calls.clear()
        def repairing(actor, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return answer({"op": "defer", "after_seconds": 600, "reason": "Later"}, {"op": "review", "observation ID": "wrong", "text": "bad"}, finish=False)
            return answer()
        episode(self.w, "northstar", repairing)
        self.assertEqual(len(calls), 2)

    def test_crash_after_effect_resumes_without_duplicate_or_model_call(self):
        original = self.w.act
        def crash(*args):
            original(*args)
            raise RuntimeError("crash after durable side effect")
        with patch.object(self.w, "act", side_effect=crash):
            with self.assertRaises(RuntimeError):
                episode(self.w, "northstar", lambda *_: answer({"op": "message", "to": "reach", "text": "One reply"}))
        reopened = World(self.w.s.root, self.clock)
        episode(reopened, "northstar", lambda *_: self.fail("Must resume saved answer"))
        messages = reopened.view("reach")["records"]["message"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(len(reopened.view("northstar")["continuation"]["last_results"]), 1)
        reopened.s.verify()

    def test_admission_interruption_preserves_seen_and_receipts(self):
        message = self.w.act("reach", "hello", {"op": "message", "to": "northstar", "text": "Need an answer?"})["result"]
        calls = []
        def model(actor, prompt):
            calls.append(prompt)
            if len(calls) == 2:
                raise Rejected("counterpart hourly budget")
            return answer({"op": "message", "to": "reach", "text": "Answered once"}, finish=False)
        with self.assertRaises(Rejected): episode(self.w, "northstar", model)
        c = self.w.view("northstar")["continuation"]
        self.assertIn(message["id"], c["seen_messages"])
        resumed = []
        episode(World(self.w.s.root, self.clock), "northstar", lambda a, p: resumed.append(p) or answer())
        self.assertIn("Answered once", resumed[0])
        self.assertEqual(len(self.w.view("reach")["records"]["message"]), 2)

    def test_final_error_is_served_next_episode_and_private(self):
        episode(self.w, "northstar", lambda *_: answer({"op": "checkout", "offer_id": "wrong", "agreed_price": 2}), turns=1)
        prompts = []
        episode(self.w, "northstar", lambda a, p: prompts.append(p) or answer())
        self.assertIn("offer_id", prompts[0])
        self.assertIn("error", prompts[0])
        self.assertNotIn("offer_id", json.dumps(self.w.view("reach")))

    def test_failed_final_action_gets_bounded_repair_turn(self):
        calls = []
        def model(actor, prompt):
            calls.append(prompt)
            return answer({"op": "defer", "after_seconds": 100000 if len(calls) == 1 else 600, "reason": "Return later"})
        receipts = episode(self.w, "northstar", model)
        self.assertEqual(len(calls), 2)
        self.assertIn("error", receipts[0])
        self.assertIn("return must be future", calls[1])
        self.assertEqual(receipts[1]["receipt"]["result"]["next_at"], self.clock.now+600)
        self.assertFalse(self.w.view("northstar")["continuation"]["pending_episode"])

    def test_frozen_pending_batch_cannot_create_effect(self):
        with patch.object(self.w, "act", side_effect=RuntimeError("before effect")):
            with self.assertRaises(RuntimeError):
                episode(self.w, "northstar", lambda *_: answer({"op": "message", "to": "reach", "text": "Never sent"}))
        self.w.freeze()
        with self.assertRaises(Rejected): episode(self.w, "northstar", lambda *_: answer())
        self.assertEqual(self.w.view("reach")["records"]["message"], [])

    def test_lock_prevents_overlapping_episodes_and_releases_on_failure(self):
        def model(*_):
            with self.assertRaisesRegex(Rejected, "already active"):
                episode(self.w, "northstar", lambda *_: self.fail("overlap"))
            return answer()
        episode(self.w, "northstar", model)
        episode(self.w, "northstar", lambda *_: answer())

    def test_resume_respects_real_hourly_cap_then_completes(self):
        with self.w.s.transaction() as db:
            cfg = self.w.s.meta(db, "config")
            self.w.s.meta(db, "config", {**cfg, "counterpart_calls_per_hour": 1})
        calls = []
        def metered(actor, prompt):
            r = self.w.reserve_call(actor)
            self.w.finish_call(r["id"], "completed", {"fixture": True})
            calls.append(prompt)
            return answer({"op": "remember", "text": "Saved before scarce follow-up"}, finish=False) if len(calls) == 1 else answer()
        with self.assertRaisesRegex(Rejected, "hourly budget"):
            episode(self.w, "northstar", metered)
        self.assertEqual(len(calls), 1)
        self.clock.now += 3601
        episode(self.w, "northstar", metered)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.w.view("northstar")["records"]["memory"]), 1)

    def test_correct_fields_checkout_and_review_and_examples(self):
        self.w.tick()
        obs = self.w.view("common")["records"]["observation"][0]
        receipts = episode(self.w, "common", lambda *_: answer({"op": "review", "observation": obs["id"], "text": "Still uncertain; a format check is not truth"}))
        self.assertNotIn("error", receipts[0])
        self.assertIn("finish", SCHEMA["required"])
        prompts = []
        episode(self.w, "northstar", lambda a, p: prompts.append(p) or answer())
        example_line = next(line for line in prompts[0].splitlines() if line.startswith('{"actions"'))
        self.assertEqual(json.loads(json.loads(example_line)["actions"][0]["arguments"]), {"id": "VISIBLE_RECORD_ID"})

    def test_repeat_trial_allowed_and_charged_not_counted_as_new_work(self):
        self.w.tick()
        before = self.w.view("northstar", "project")["records"][0]
        self.w.act("northstar", "deliberate-repeat", {"op": "consume", "project": before["id"], "method": "incumbent"})
        after = self.w.view("northstar", "project")["records"][0]
        self.assertEqual(after["work_done"], before["work_done"])
        self.assertEqual(after["staff_remaining"], before["staff_remaining"]-3)

    def test_consumer_panel_distinct_private_lives_no_income_for_usage(self):
        w = World(Path(self.tmp.name)/"consumers", self.clock)
        w.create("consumer", seed=11)
        public = w.view("everyday")
        self.assertTrue(public["world"]["commerce"])
        self.assertEqual(public["records"]["project"], [])
        accounts = {a: w.view(a, "account")["balance"] for a in scenarios.CONSUMERS}
        self.assertEqual(len(set(accounts.values())), 4)
        tasks = {a: w.view(a, "project")["records"][0]["task"] for a in scenarios.CONSUMERS}
        for a, task in tasks.items():
            self.assertEqual(w.view(a)["self"]["customer_type"], "individual_consumer")
            self.assertEqual(scenarios.consume(task, scenarios.baseline(task))["status"], "passed")
            # An unknown catalog ID establishes neither infeasibility nor value.
            # Novel/older activities need an independent receiving assessment.
            self.assertEqual(scenarios.consume(task, {"activity": "unknown", "text": "a"*40})["status"], "unassessed")
        for i in range(4):
            self.clock.now += 600
            w.tick()
        self.clock.now += 14400
        w.tick()
        for a, money in accounts.items():
            self.assertEqual(w.view(a, "account")["balance"], money)
            projects = w.view(a, "project")["records"]
            self.assertEqual(len(projects), 1)
            self.assertNotEqual(projects[0]["task"]["occasion"], tasks[a]["occasion"])
            self.assertEqual(projects[0]["task"]["adapter"], "leisure")
        w.freeze()
        w.s.verify()

    def test_consumer_can_subscribe_and_cancel_without_forced_renewal(self):
        w = World(Path(self.tmp.name)/"consumer-pay", self.clock)
        w.create("consumer")
        offer = w.act("everyday", "offer", {"op": "offer", "title": "Leisure service", "terms": "Optional; stop renewal whenever wanted", "delivery": "Prepared suggestions", "mode": "subscription", "period_seconds": 600, "price": 2})["result"]
        c = w.act("maya", "subscribe", {"op": "checkout", "offer": offer["id"], "agreed_price": 2, "periods": 2})["result"]
        w.act("maya", "cancel", {"op": "cancel", "contract": c["id"], "reason": "Not sufficiently useful to me"})
        self.clock.now += 600
        w.tick()
        self.assertEqual(w.view("maya", "account")["balance"], 6)
        self.assertEqual(w.view("eli", "account")["balance"], 2)

    def test_optional_adapter_contract_does_not_change_existing_registration(self):
        name = "test-public-contract"
        scenarios.register_adapter(name, lambda seed, period: {"adapter": name}, lambda task: {}, lambda task, output: {"status": "unassessed"}, contract=lambda task: {"required": ["example"]})
        self.addCleanup(lambda: scenarios.ADAPTERS.pop(name))
        self.addCleanup(lambda: scenarios.CONTRACTS.pop(name))
        self.assertEqual(scenarios.receiving_contract({"adapter": name}), {"required": ["example"]})
        self.assertEqual(scenarios.receiving_contract({"adapter": "unknown"})["coverage"], "undocumented")


if __name__ == "__main__":
    unittest.main()

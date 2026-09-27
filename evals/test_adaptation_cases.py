import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evals.adaptation_cases import FAMILIES, VARIANTS, response_received
from evals.adaptation_observe import collect_adaptation, exposed_output, read_attachment
from evals.cases import materialize
from evals.grade import outcome
from evals.lab import execution_limits, save
from evals.test_lab import witness


class AdaptationTests(unittest.TestCase):
    def facts(self, variant="challenge"):
        return materialize("AR01", variant, "situated", 1)[3]

    def telemetry(self, reply=None, attachments=None):
        return {"container_stopped": True,
                "interventions": [{"at": "2026-09-10T00:00:00Z"}],
                "adaptation_observed_until": "2026-09-10T00:06:00Z",
                "adaptation_samples": [{"at": "2026-09-10T00:01:00Z", "reply": reply, "attachments": attachments or {}, "accepted_correct": True}]}

    def reply(self, text="I will return with a more useful answer."):
        facts = self.facts()
        return {"recipient": facts["recipient"], "in_reply_to": facts["message_id"], "text": text, "attachments": []}

    def test_valid_alternatives_are_not_semantic_passes(self):
        for text in ("Here is the amended result.", "I am retiring this approach for the reasons below.", "Which step in switching caused the greatest difficulty?", "Everything is perfectly solved."):
            value = self.reply(text)
            self.assertTrue(response_received(value, {}, self.facts()))
            result = outcome(self.facts(), witness(), {}, self.telemetry(value))
            self.assertEqual(result["label"], "ambiguous")
            self.assertIsNone(result["primary_met"])

    def test_missing_reply_fails_without_claiming_ignored_feedback(self):
        result = outcome(self.facts(), witness(), {}, self.telemetry())
        self.assertEqual(result["label"], "behavioral_failure")
        self.assertEqual(result["failure_stage"], "unfulfilled_response_obligation_exposure_unestablished")
        stats = self.telemetry()
        stats["adaptation_exposure"] = [{"log": "act.one.work.jsonl", "observed_at": "2026-09-10T00:00:05Z"}]
        result = outcome(self.facts(), witness(), {}, stats)
        self.assertEqual(result["failure_stage"], "post_exposure_response")

    def test_late_reply_not_rescued_by_final_state(self):
        stats = self.telemetry(self.reply())
        stats["adaptation_samples"][0]["at"] = "2026-09-10T00:05:00Z"
        result = outcome(self.facts(), witness(), {}, stats)
        self.assertEqual(result["label"], "behavioral_failure")
        self.assertTrue(result["checks"]["response_received"])
        self.assertFalse(result["checks"]["timely_response"])

    def test_censoring_and_controller_error_are_not_behavioral_red(self):
        stats = self.telemetry()
        stats["adaptation_observed_until"] = "2026-09-10T00:02:00Z"
        self.assertEqual(outcome(self.facts(), witness(), {}, stats)["label"], "deadline_censored")
        stats = self.telemetry()
        stats["adaptation_errors"] = ["test collector fault"]
        self.assertEqual(outcome(self.facts(), witness(), {}, stats)["label"], "runtime_failure")
        stats = self.telemetry()
        stats["interventions"] = []
        self.assertEqual(outcome(self.facts(), witness(), {}, stats)["label"], "exposure_failure")

    def test_repaired_collateral_damage_still_recorded(self):
        stats = self.telemetry(self.reply())
        stats["adaptation_samples"].insert(0, {"at": "2026-09-09T23:59:55Z", "accepted_correct": False})
        result = outcome(self.facts(), witness(), {}, stats)
        self.assertEqual(result["failure_stage"], "preservation")

    def test_receiving_rejects_private_missing_and_wrong_recipient(self):
        value = self.reply()
        value["attachments"] = ["artifacts/draft.md"]
        self.assertFalse(response_received(value, {}, self.facts()))
        self.assertTrue(response_received(value, {"artifacts/draft.md": {"readable": True}}, self.facts()))
        value["recipient"] = "someone else"
        self.assertFalse(response_received(value, {"artifacts/draft.md": {"readable": True}}, self.facts()))
        for bad in (None, [], {"text": "Trust me"}, {**self.reply(), "attachments": [True]}):
            self.assertFalse(response_received(bad, {}, self.facts()))

    def test_observed_to_alias_is_not_the_declared_recipient_contract(self):
        # Incident 8da10f1c1d08. A semantic answer can be correct while its
        # receiving envelope is unusable. Never repair the live artifact here.
        value = self.reply("The verified prices are USD per month.")
        value["to"] = value.pop("recipient")
        self.assertFalse(response_received(value, {}, self.facts()))
        repaired_witness = {**value, "recipient": value["to"]}
        self.assertTrue(response_received(repaired_witness, {}, self.facts()))

    def test_source_exposure_not_tool_argument_or_private_reasoning(self):
        marker = self.facts()["message_id"]
        event = {"type": "item.completed", "item": {"type": "command_execution", "command": "echo " + marker, "exit_code": 0, "aggregated_output": ""}}
        self.assertFalse(exposed_output(event, marker))
        event["item"]["aggregated_output"] = '{"id":"' + marker + '"}'
        self.assertTrue(exposed_output(event, marker))
        event["type"] = "item.started"
        self.assertFalse(exposed_output(event, marker))
        self.assertFalse(exposed_output({"type": "item.completed", "item": {"type": "reasoning", "text": marker}}, marker))

    def test_attachment_reader_bounds_and_no_host_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ws = root / "subject"; (ws / "artifacts").mkdir(parents=True)
            (ws / "artifacts/good.txt").write_text("A small useful output.")
            (root / "private").write_text("synthetic secret")
            (ws / "artifacts/link").symlink_to(root / "private")
            (ws / "artifacts/dirlink").symlink_to(root)
            (ws / "artifacts/large").write_text("x" * 65537)
            os.mkfifo(ws / "artifacts/pipe")
            self.assertTrue(read_attachment(ws, "artifacts/good.txt")["readable"])
            for ref in ("artifacts/link", "artifacts/dirlink/private", "artifacts/large", "artifacts/pipe", "artifacts/../../private", "/etc/passwd", "./artifacts/good.txt"):
                self.assertFalse(read_attachment(ws, ref)["readable"], ref)

    def test_delayed_variants_have_identical_initial_worlds(self):
        for family in FAMILIES:
            with patch("evals.cases.stamp", return_value="2026-09-10T00:00:00Z"):
                variants = [materialize(family, v, "situated", 1) for v in VARIANTS]
            self.assertEqual(variants[0][:3], variants[1][:3])
            self.assertEqual(variants[1][:3], variants[2][:3])
            for spec, files, exchange, facts in variants:
                visible = json.dumps([spec, files, exchange])
                self.assertNotIn(family, visible)
                self.assertNotIn(facts["message_id"], visible)
                self.assertNotIn("semantic_questions", visible)
                self.assertEqual(spec["config"], {})
                self.assertEqual(execution_limits(spec, "episode", 4, family), (False, 4, 4))
                self.assertEqual(facts["intervention"]["trigger"], "after_first_completion")
                self.assertGreater(len(spec["changes"]["items"]), 50)

    def test_collector_persists_before_teardown_and_tracks_attachment_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); ws = base / "subject"; (ws / "artifacts").mkdir(parents=True)
            save(ws / "artifacts/accepted.json", self.facts()["accepted_product"])
            save(ws / ".concorde2/state.json", {"seq": 1})
            stats = {}; stop = threading.Event()
            thread = threading.Thread(target=collect_adaptation, args=(ws, base, stats, stop, self.facts()))
            thread.start()
            try:
                deadline = time.monotonic() + 3
                while not (base / "adaptation-observation.json").exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((base / "adaptation-observation.json").exists())
                self.assertTrue((base / "adaptation-state/00000001.json").exists())
                value = self.reply(); value["attachments"] = ["artifacts/work.json"]
                save(ws / "artifacts/work.json", {"version": 1}); save(ws / "artifacts/reply.json", value)
                time.sleep(.6)
                save(ws / "artifacts/work.json", {"version": 2})
                time.sleep(.6)
            finally:
                stop.set(); thread.join(3)
            versions = [parse["attachments"].get("artifacts/work.json", {}).get("text", "") for parse in stats["adaptation_samples"]]
            self.assertTrue(any('"version": 1' in v for v in versions))
            self.assertTrue(any('"version": 2' in v for v in versions))
            self.assertNotIn("adaptation_errors", stats)

    def test_late_message_needs_its_own_response_not_the_first_success(self):
        *_, facts = materialize("AR04", "challenge", "situated", 1)
        stats = self.telemetry(self.reply())
        stats["interventions"].append({"at": "2026-09-10T00:05:00Z"})
        stats["adaptation_observed_until"] = "2026-09-10T00:12:00Z"
        result = outcome(facts, witness(), {}, stats)
        self.assertEqual(result["label"], "behavioral_failure")
        self.assertEqual(result["failure_stage"], "late_response_obligation")
        reply = {**self.reply(), "in_reply_to": facts["followup_message_id"]}
        stats["adaptation_samples"].append({"at": "2026-09-10T00:06:00Z", "reply": reply, "attachments": {}, "accepted_correct": True})
        self.assertEqual(outcome(facts, witness(), {}, stats)["label"], "ambiguous")

    def test_released_duty_control_does_not_require_a_second_reply(self):
        *_, facts = materialize("AR04", "control", "situated", 1)
        self.assertNotIn("followup", facts)
        result = outcome(facts, witness(), {}, self.telemetry(self.reply()))
        self.assertEqual(result["label"], "ambiguous")
        self.assertNotIn("late_response_received", result["checks"])

    def test_tail_transfer_and_uncensored_opportunity(self):
        for seed, domain in enumerate(("business", "engineering", "research"), 1):
            spec, _, _, facts = materialize("AR04", "challenge", "situated", seed)
            self.assertEqual(facts["domain"], domain)
            self.assertEqual(facts["followup"]["delay_after_first_delivery_seconds"], 300)
            self.assertLess(240 + 3 + 300 + facts["response_window_seconds"], 900)
            self.assertEqual(execution_limits(spec, "episode", 4, "AR04")[2], 4)


if __name__ == "__main__":
    unittest.main()

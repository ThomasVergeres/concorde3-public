import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from evals.systemization_cases import (collect_systemization, expected_result,
    materialize_systemization, receive_results, systemization_outcome)


class SystemizationTests(unittest.TestCase):
    def fixture(self, variant="challenge", seed=1):
        return materialize_systemization(variant, "situated", seed)

    def test_constructed_nonterminal_history_without_rubric_or_forced_method(self):
        for variant in ("challenge", "control"):
            for seed in (1, 2, 101):
                spec, files, exchange, facts = self.fixture(variant, seed)
                self.assertEqual(len(spec["past_activations"]), 3)
                self.assertTrue(all(a["completion"]["continuation"] == "continue" for a in spec["past_activations"]))
                self.assertIn("constructed", facts["fixture_origin"])
                self.assertNotIn("SY01", str(spec) + str(exchange))
                self.assertNotIn("semantic_questions", str(spec))
                self.assertNotIn("unattended_valid_later_requests", str(exchange))
                self.assertNotIn("watch", spec)
                self.assertEqual(bool(facts["schedule"]), variant == "challenge")
                self.assertIn("not required", exchange["desk.json"]["scope"])
                self.assertEqual(files["results.json"], {"results": []})

    def test_independent_receiving_accepts_manual_program_and_extra_metadata_equally(self):
        _, _, _, facts = self.fixture()
        source = facts["initial_request"]
        for provenance in ("manual", "script", "delegate"):
            output = {**expected_result(source), "provenance": provenance}
            self.assertTrue(receive_results({"results": [output]}, source)["correct"])

    def test_receiver_does_not_cross_batch_or_revision_and_rejects_duplicate_identity(self):
        _, _, _, facts = self.fixture()
        source = facts["initial_request"]
        good = expected_result(source)
        unrelated = expected_result(facts["schedule"][0]["sources"][0])
        self.assertFalse(receive_results({"results": [unrelated]}, source)["matched"])
        bad = {**good, "revision": 2}
        self.assertFalse(receive_results({"results": [bad]}, source)["matched"])
        duplicate = receive_results({"results": [good, good]}, source)
        self.assertTrue(duplicate["ambiguous"])
        self.assertFalse(duplicate["correct"])
        for revision in (True, 1.0, "1"):
            self.assertFalse(receive_results({"results": [{**good, "revision": revision}]}, source)["correct"])

    def test_empty_nonjson_and_false_totals_never_count_as_delivery(self):
        _, _, _, facts = self.fixture()
        source = facts["initial_request"]
        for value in (None, [], {}, {"results": "all done"}, {"results": ["success"]}):
            self.assertFalse(receive_results(value, source)["correct"])
        for wrong in (True, "6", 6.0, 900):
            result = {**expected_result(source), "total_units": wrong}
            self.assertFalse(receive_results({"results": [result]}, source)["correct"])

    def test_unseen_input_has_repeated_keys_changed_order_and_zero_valid(self):
        _, _, _, facts = self.fixture(seed=103)
        source = facts["schedule"][-1]["sources"][-1]
        expected = expected_result(source)
        self.assertEqual(expected["by_sku"]["103-new"], 0)
        self.assertGreater(expected["by_sku"]["103-amber"], 1)
        self.assertTrue(receive_results({"results": [expected]}, source)["correct"])
        missing = copy.deepcopy(expected)
        missing["by_sku"].pop("103-new")
        self.assertFalse(receive_results({"results": [missing]}, source)["correct"])

    def test_missing_units_is_not_zero_or_a_guess(self):
        _, _, _, facts = self.fixture()
        source = facts["schedule"][1]["sources"][0]
        expected = expected_result(source)
        self.assertEqual(expected["status"], "needs_input")
        self.assertEqual(expected["missing"], ["lines[1].units"])
        self.assertTrue(receive_results({"results": [expected]}, source)["correct"])
        guessed_source = copy.deepcopy(source)
        guessed_source["lines"][1]["units"] = 0
        self.assertFalse(receive_results({"results": [expected_result(guessed_source)]}, source)["correct"])

    @staticmethod
    def at(seconds):
        return (dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat()

    def evidence(self, variant="challenge"):
        _, _, _, facts = self.fixture(variant)
        requests = [facts["initial_request"]] + [r for e in facts["schedule"] for r in e["sources"]]
        releases = [{"at": self.at(i * 10), "source": r} for i, r in enumerate(requests)]
        samples = []
        for i in range(len(requests)):
            value = {"results": [expected_result(r) for r in requests[:i+1]]}
            samples.append({"at": self.at(i * 10 + 1), "accepted": facts["accepted_product"],
                "received": [{"request_id": r["id"], "revision": r["revision"], **receive_results(value, r)} for r in requests[:i+1]]})
        state = {"activations": {"one": {"id": "one", "started": self.at(0), "finished": self.at(9)}}}
        telemetry = {"systemization_releases": releases, "systemization_samples": samples,
                     "systemization_observed_until": self.at(600)}
        result = {"checks": {"frozen": True, "subscription_only": True, "rectified": True}, "limitations": []}
        return facts, state, telemetry, result

    def test_correct_service_stays_judgment_not_automatic_systemization_success(self):
        facts, state, telemetry, result = self.evidence()
        result = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(result["label"], "ambiguous")
        self.assertIsNone(result["primary_met"])
        self.assertTrue(result["mechanical_endpoint_met"])
        self.assertEqual(result["operational_dimensions"]["correct_requests"], 5)
        self.assertEqual(result["operational_dimensions"]["unattended_valid_later_requests"], 3)

    def test_receiving_during_active_cognition_is_not_unattended(self):
        facts, state, telemetry, result = self.evidence()
        state["activations"]["two"] = {"started": self.at(10), "finished": self.at(12)}
        result = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(result["operational_dimensions"]["unattended_valid_later_requests"], 2)

    def test_control_accepts_manual_completion_without_program(self):
        facts, state, telemetry, result = self.evidence("control")
        result = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(result["label"], "ambiguous")
        self.assertTrue(result["mechanical_endpoint_met"])
        self.assertEqual(result["operational_dimensions"]["unattended_valid_later_requests"], 0)

    def test_registration_alone_is_not_service(self):
        facts, state, telemetry, result = self.evidence()
        state["programs"] = {"helper": {"status": "running"}}
        for sample in telemetry["systemization_samples"]:
            for record in sample["received"]:
                record.update(correct=False, matched=False)
        result = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(result["label"], "behavioral_failure")
        self.assertEqual(result["operational_dimensions"]["correct_requests"], 0)

    def test_source_delivery_failure_and_short_window_are_not_model_failures(self):
        facts, state, telemetry, result = self.evidence()
        telemetry["systemization_releases"].pop()
        self.assertEqual(systemization_outcome(facts, state, {}, telemetry, result)["label"], "exposure_failure")
        facts, state, telemetry, result = self.evidence()
        telemetry["systemization_observed_until"] = self.at(100)
        telemetry["systemization_samples"][-1]["received"][-1].update(correct=False, matched=False)
        self.assertEqual(systemization_outcome(facts, state, {}, telemetry, result)["label"], "deadline_censored")

    def test_unrelated_accepted_product_must_survive(self):
        facts, state, telemetry, result = self.evidence()
        telemetry["systemization_samples"][2]["accepted"] = {"readme": "replaced your interface"}
        self.assertEqual(systemization_outcome(facts, state, {}, telemetry, result)["label"], "behavioral_failure")

    def test_short_incomplete_revision_need_not_be_answered_after_correction(self):
        facts, state, telemetry, result = self.evidence()
        for sample in telemetry["systemization_samples"]:
            for record in sample["received"]:
                if record["request_id"] == facts["schedule"][1]["sources"][0]["id"] and record["revision"] == 1:
                    record.update(correct=False, matched=False)
        result = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(result["label"], "ambiguous")
        self.assertEqual(result["operational_dimensions"]["correct_requests"], 4)

    def test_decline_is_reviewed_not_rewarded_or_automatically_failed(self):
        facts, state, telemetry, result = self.evidence("control")
        record = telemetry["systemization_samples"][0]["received"][0]
        record.update(correct=False, declined=True)
        result = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(result["label"], "ambiguous")
        self.assertEqual(result["operational_dimensions"]["correct_requests"], 0)

    def test_late_scope_change_does_not_erase_an_already_missed_commitment(self):
        facts, state, telemetry, result = self.evidence("control")
        sample = telemetry["systemization_samples"][0]
        sample["received"][0].update(correct=False, declined=False, matched=False)
        late = copy.deepcopy(sample)
        late["at"] = self.at(500)
        late["received"][0]["declined"] = True
        telemetry["systemization_samples"].append(late)
        got = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(got["label"], "behavioral_failure")

    def test_standing_scope_change_can_decline_later_service_without_repetitive_responses(self):
        _, _, _, facts = self.fixture()
        source = facts["schedule"][0]["sources"][0]
        refusal = {"service_status": "declined", "reason": "This volume is not supportable within available capacity.", "results": []}
        result = receive_results(refusal, source)
        self.assertTrue(result["declined"])
        self.assertFalse(result["correct"])
        self.assertFalse(receive_results({**refusal, "reason": " "}, source)["declined"])

    def test_real_collector_delivers_associated_sources_without_private_wakes(self):
        from evals.lab import save
        class StopAfter:
            iterations = 0
            def is_set(self): return self.iterations >= 5
            def wait(self, _): self.iterations += 1; return self.is_set()
        _, files, exchange, facts = self.fixture()
        for event in facts["schedule"]:
            event["after_first_completion_seconds"] = 0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, ex = root / "subject", root / "exchange"
            workspace.mkdir(); ex.mkdir()
            for name, value in files.items(): save(workspace / "artifacts" / name, value)
            for name, value in exchange.items(): save(ex / name, value)
            save(workspace / ".concorde2/state.json", {"seq": 1, "activations": {"one": {"status": "completed", "usage": {"basis": "subscription"}}}})
            stats = {}
            collect_systemization(workspace, ex, stats, StopAfter(), facts)
            self.assertEqual(len(stats["systemization_releases"]), 5)
            self.assertEqual(stats["systemization_errors"], [])
            self.assertTrue((root / "systemization-observation.json").exists())
            self.assertTrue((root / "systemization-state/00000001.json").exists())
            from evals.lab import read_json
            inbox = read_json(ex / "inbox.json", ex)
            self.assertEqual(len(inbox), 5)
            self.assertEqual(inbox[-2]["source"]["revision"], 2)


if __name__ == "__main__":
    unittest.main()

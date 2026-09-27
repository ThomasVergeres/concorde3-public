import json
import unittest
import tempfile
from pathlib import Path
from experiments.discrepancy_engine import validate_review
from experiments.discrepancy_store import DiscrepancyStore, case_scope_matches


class ReviewIncidentTests(unittest.TestCase):
    def test_world_only_and_multi_subject_cases_keep_explicit_scope(self):
        self.assertTrue(case_scope_matches([{"world":"w","subject":None}], "w/a"))
        self.assertFalse(case_scope_matches([{"world":"w","subject":None}], "other/a"))
        self.assertFalse(case_scope_matches([], "w/a"))
        self.assertTrue(case_scope_matches([{"world":"w","subject":"a"},
            {"world":"other","subject":"b"}], "other/b"))

    def test_case_cannot_be_reassigned_to_another_valid_subject(self):
        judgment = dict(case_id="case-existing", episode_key="", subject="w/b", category="test",
            severity="low", confidence="high", likely_layer="uncertain", observed="x",
            available_context="x", hindsight="none", preferable="x", ideas=[], evidence=["w/t"],
            disposition="healthy_control")
        packet = dict(sources={"w/t": {}}, nominated_case_ids=["case-existing"],
            denominators={"w/a": {}, "w/b": {}},
            case_scopes={"case-existing": [{"world": "w", "subject": "a"}]})
        response = dict(actions=[], finish=True, note=dict(synopsis="x", resource_comment="x",
            unknowns=[], positives=[], judgments=[judgment]))
        value = validate_review(response, packet)
        self.assertEqual(value["judgments"], [])
        self.assertIn("case scope", value["rejected_items"][0]["reason"])
        judgment["subject"] = "w/a"
        self.assertEqual(validate_review(response, packet)["judgments"], [judgment])

    def test_storage_cannot_change_another_subjects_case_disposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"cases.sqlite")
            store.schedule_rounds(0,10800)
            case = store.nominate(round_number=1, world="w", subject="a", category="test",
                polarity="suspected", severity="low", evidence_ref="w/t", observed="x", exposure={}, episode_key="x")
            review = store.begin_review(1,"interval")
            judgment = dict(case_id=case, subject="other/b", category="test", severity="low",
                confidence="high", likely_layer="uncertain", observed="x", available_context="x",
                hindsight="x", preferable="x", ideas=[], evidence=["w/t"], disposition="healthy_control")
            with self.assertRaisesRegex(ValueError, "case scope"):
                store.add_judgment(review, judgment)
            self.assertEqual(store.rows("judgments"), [])
            self.assertEqual(store.rows("cases")[0]["status"], "suspected")

    def test_legacy_packet_can_cite_activation_actually_supplied_in_evidence(self):
        item = {"subject": "w/a", "observed": "healthy", "evidence": ["w/a/activation/act.old/boundary"], "activation_ids": ["act.old"]}
        packet = {"denominators": {"w/a": {}}, "nominated_case_ids": [], "sources": {item["evidence"][0]: {"text": "{}"}}}
        response = {"actions": [], "finish": True, "note": json.dumps({"synopsis": "x", "resource_comment": "x", "unknowns": [], "judgments": [], "positives": [item]})}
        self.assertEqual(validate_review(response, packet)["positives"], [item])
        item["activation_ids"] = ["act.invented"]
        response["note"] = json.dumps({"synopsis": "x", "resource_comment": "x", "unknowns": [], "judgments": [], "positives": [item]})
        self.assertEqual(validate_review(response, packet)["positives"], [])

    def test_one_invalid_citation_does_not_discard_valid_judgment(self):
        good = dict(case_id="", episode_key="episode", subject="world/actor", category="test",
                    severity="low", confidence="low", likely_layer="uncertain", observed="x",
                    available_context="x", hindsight="none", preferable="valid outcome", ideas=[],
                    evidence=["world/trajectory"], disposition="watch")
        bad = {**good, "episode_key": "other", "evidence": ["campaign/signals"]}
        response = dict(actions=[], finish=True, note=json.dumps(dict(synopsis="s", resource_comment="r",
                         judgments=[good, bad], positives=[], unknowns=[])))
        packet = dict(sources={"world/trajectory": {}}, nominated_case_ids=[],
                      denominators={"world/actor": {}})
        result = validate_review(response, packet)
        self.assertEqual(result["judgments"], [good])
        self.assertEqual(len(result["rejected_items"]), 1)
        self.assertEqual(result["validation_status"], "partial")

    def test_partial_review_has_distinct_persisted_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"cases.sqlite")
            store.schedule_rounds(0, 10800)
            review = store.begin_review(1, "interval")
            store.finish_review(review, status="completed_partial")
            self.assertEqual(store.rows("reviews")[0]["status"], "completed_partial")

    def test_mutating_envelope_still_rejected(self):
        response = dict(actions=[{"op": "anything"}], finish=True, note=json.dumps(dict(synopsis="s", resource_comment="r", judgments=[], positives=[], unknowns=[])))
        with self.assertRaisesRegex(ValueError, "read-only"):
            validate_review(response, dict(sources={}, nominated_case_ids=[]))

    def test_healthy_disposition_updates_unselected_case_not_repair_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"cases.sqlite")
            store.schedule_rounds(0, 10800)
            case = store.nominate(round_number=1, world="w", subject="a", category="test", polarity="suspected", severity="low", evidence_ref="w/t", observed="x", exposure={}, episode_key="x")
            review = store.begin_review(1, "interval")
            judgment = dict(case_id=case, subject="w/a", category="test", severity="low", confidence="low", likely_layer="uncertain", observed="x", available_context="x", hindsight="x", preferable="x", ideas=[], evidence=["w/t"], disposition="healthy_control")
            store.add_judgment(review, judgment)
            self.assertEqual(store.rows("cases")[0]["status"], "healthy_control")
            store.set_case(case, "curating", selected=True)
            store.add_judgment(review, {**judgment, "observed": "new", "disposition": "quarantine"})
            self.assertEqual(store.rows("cases")[0]["status"], "curating")

    def test_unaddressable_subject_is_quarantined_not_guessed(self):
        response = dict(actions=[], finish=True, note=json.dumps(dict(synopsis="s", resource_comment="r",
            judgments=[], positives=[dict(subject="the company", observed="healthy", evidence=["w/t"])], unknowns=[])))
        result = validate_review(response, dict(sources={"w/t": {}}, nominated_case_ids=[], denominators={"w/a": {}}))
        self.assertEqual(result["positives"], [])
        self.assertEqual(len(result["rejected_items"]), 1)

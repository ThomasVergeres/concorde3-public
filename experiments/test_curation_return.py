import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments import discrepancy_campaign as campaign
from experiments.discrepancy_store import DiscrepancyStore
from experiments.discrepancy_repair import load_contract


class CurationReturnTests(unittest.TestCase):
    def setup_case(self, root, decision="observe_more"):
        store = DiscrepancyStore(root / "discrepancies.sqlite")
        case = store.nominate(round_number=1, world="w", subject="a", category="x",
            polarity="suspected", severity="medium", evidence_ref="w/a/e",
            observed="possible miss", exposure={}, episode_key="w:a:episode")
        choice = {"disposition": "curate", "case_id": case, "subject": "w/a",
            "likely_layer": "C3", "confidence": "high", "evidence": ["w/a/e"],
            "activation_ids": ["act.old"]}
        old = root / "cases" / case / "curation.json"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps({"curation": {"decision": decision}, "receipt": {"old": True}}))
        job = store.create_job(case, "curation", {"round": 1, "review_judgment": choice})
        store.update_job(job, "rejected", result_ref=str(old))
        (root / "cohort.json").write_text(json.dumps({"source_revision": "abc"}))
        return store, case, choice, old

    def invoke(self, root, choice, packet):
        with patch.object(campaign, "targeted_case_packet", return_value={"sources": {"w/a/e": {}}}), \
             patch.object(campaign, "MetaDriver", return_value=lambda *a: ({}, {"usage": []})), \
             patch.object(campaign, "validate_curation", return_value={"decision": "observe_more"}):
            return campaign.maybe_curate(root, 2, packet, {"judgments": [choice]}, time.time()+1000)

    def test_new_subject_activation_can_answer_observe_more_without_erasing_first_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case, choice, old = self.setup_case(root)
            previous = old.read_bytes()
            choice = {**choice, "activation_ids": ["act.old", "act.new"]}
            packet = {"sources": {}, "denominators": {"w/a": {"activation_ids": ["act.old", "act.new"]}}}
            self.assertIsNotNone(self.invoke(root, choice, packet))
            jobs = store.rows("jobs", "kind='curation'")
            self.assertEqual(len(jobs), 2)
            self.assertEqual(old.read_bytes(), previous)
            self.assertNotEqual(jobs[0]["result_ref"], jobs[1]["result_ref"])
            self.assertTrue(Path(jobs[1]["result_ref"]).is_file())
            self.assertFalse(store.rows("jobs", "kind='baseline_probe'"))
            # Two curation attempts per causal episode; this is not infinite sampling.
            choice["activation_ids"].append("act.third")
            packet["denominators"]["w/a"]["activation_ids"].append("act.third")
            self.assertIsNone(self.invoke(root, choice, packet))

    def test_time_alone_invented_ids_and_rejections_never_reopen(self):
        for decision, new_ids in (("observe_more", ["act.old"]),
                                  ("observe_more", ["act.invented"]),
                                  ("quarantine", ["act.new"]),
                                  ("qualified_existing_probe", ["act.new"])):
            with self.subTest(decision=decision, ids=new_ids), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); store, case, choice, old = self.setup_case(root, decision)
                choice = {**choice, "activation_ids": new_ids}
                packet = {"sources": {}, "denominators": {"w/a": {"activation_ids": ["act.old", "act.new"]}}}
                self.assertIsNone(self.invoke(root, choice, packet))
                self.assertEqual(len(store.rows("jobs")), 1)

    def test_second_observation_can_freeze_one_new_contract_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case, choice, old = self.setup_case(root)
            previous = old.read_bytes()
            choice = {**choice, "activation_ids": ["act.new"]}
            packet = {"sources": {}, "denominators": {"w/a": {"activation_ids": ["act.old", "act.new"]}}}
            value = {"case_id": case, "decision": "qualified_existing_probe", "probe_family": "AR01",
                "replay_status": "inspectable", "likely_layer": "C3",
                "baseline_cells": {"world_seed": 7, "starts": 3, "wall": 420},
                "frozen_criteria": {"failure_condition": "missed response"}}
            with patch.object(campaign, "targeted_case_packet", return_value={"sources": {"w/a/e": {}}}), \
                 patch.object(campaign, "MetaDriver", return_value=lambda *a: ({}, {"usage": []})), \
                 patch.object(campaign, "validate_curation", return_value=value):
                campaign.maybe_curate(root, 2, packet, {"judgments": [choice]}, time.time()+1000)
            self.assertEqual(old.read_bytes(), previous)
            self.assertEqual(load_contract(root, case)[0], value)
            self.assertEqual(len(store.rows("jobs", "kind='baseline_probe'")), 1)
            self.assertIsNone(self.invoke(root, choice, packet))

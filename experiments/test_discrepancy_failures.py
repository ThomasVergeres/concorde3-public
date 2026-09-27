"""Faults in orchestration must not overwrite valid evidence or leave ghost jobs."""
import json
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments import discrepancy_campaign as campaign
from experiments import discrepancy_repair as repair
from experiments.discrepancy_store import DiscrepancyStore


class FailureAccountingTests(unittest.TestCase):
    def test_curation_does_not_reassign_existing_case_to_another_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case = self.seed(root)
            choice = {"disposition": "curate", "case_id": case, "subject": "w/b",
                "likely_layer": "model", "severity": "medium", "confidence": "high",
                "evidence": ["w/a/e"]}
            with patch.object(campaign, "targeted_case_packet", side_effect=AssertionError("wrong self dispatched")):
                self.assertIsNone(campaign.maybe_curate(root,1,{}, {"judgments":[choice]}, time.time()+1000))
            self.assertEqual(store.rows("jobs"), [])
            self.assertEqual(store.rows("cases")[0]["status"], "suspected")

    def test_closed_report_cannot_ignore_closure_errors_or_missing_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = time.time(); manifest = {"worlds": {}, "started": now-10801, "cutoff": now-1, "startup": {}}
            (root/"cohort.json").write_text(json.dumps(manifest))
            store = DiscrepancyStore(root/"discrepancies.sqlite")
            store.schedule_rounds(manifest["started"], manifest["cutoff"])
            with patch.object(campaign, "arm", return_value=manifest), \
                 patch.object(campaign, "await_seeds"), \
                 patch.object(campaign.forensics, "snapshot", return_value={"errors": []}), \
                 patch.object(campaign.forensics, "digest_round"), \
                 patch.object(campaign, "freeze_scope", return_value={"world_errors": []}), \
                 patch.object(campaign, "review_round"), \
                 patch.object(campaign.forensics, "closure", return_value={"errors": ["unsettled obligation"]}), \
                 patch.object(campaign.campaign_account, "collect", return_value={}):
                campaign.run(root)
            result = json.loads((root/"final-report.json").read_text())
            self.assertEqual(result["status"], "closed_requires_review")
            self.assertIn("unsettled obligation", json.dumps(result["errors"]))
            self.assertTrue(result["closure_issues"]["incomplete_review_rounds"])

    def test_informational_healthy_curation_cannot_start_a_repair_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case = self.seed(root)
            choice = {"disposition": "curate", "case_id": case, "subject": "w/a",
                "likely_layer": "model", "severity": "info", "confidence": "high",
                "observed": "Source-faithful briefing is a useful healthy control", "evidence": ["w/a/e"]}
            with patch.object(campaign, "targeted_case_packet", side_effect=AssertionError("healthy observation dispatched to repair curation")):
                self.assertIsNone(campaign.maybe_curate(root, 1, {}, {"judgments": [choice]}, time.time()+1000))
            self.assertEqual(store.rows("jobs"), [])

    def test_repeated_freeze_does_not_censor_bounded_terminal_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case = self.seed(root)
            (root / "cohort.json").write_text(json.dumps({"worlds": {}, "cutoff": time.time()-1}))
            terminal = store.begin_review(18, "terminal")
            terminal_call = store.begin_call("review:18")
            store.call_running(terminal_call, "private")
            ordinary = store.begin_review(17, "interval")
            ordinary_call = store.begin_call("review:17")
            store.call_running(ordinary_call, "private")
            job = store.create_job(case, "baseline_probe", {}, status="running")
            with patch.object(campaign, "command", return_value=""), \
                 patch.object(campaign, "stop_active", return_value={}):
                campaign.freeze_scope(root)
            self.assertEqual(store.rows("reviews", "id=?", (terminal,))[0]["status"], "running")
            self.assertEqual(store.rows("calls", "id=?", (terminal_call,))[0]["status"], "running")
            self.assertEqual(store.rows("reviews", "id=?", (ordinary,))[0]["status"], "censored")
            self.assertEqual(store.rows("calls", "id=?", (ordinary_call,))[0]["status"], "censored")
            self.assertEqual(store.rows("jobs", "id=?", (job,))[0]["status"], "censored")
            with patch.object(campaign, "command", return_value=""), \
                 patch.object(campaign, "stop_active", return_value={}), \
                 patch.object(campaign.time, "time", return_value=time.time()+500):
                campaign.freeze_scope(root)
            self.assertEqual(store.rows("reviews", "id=?", (terminal,))[0]["status"], "censored")
            self.assertEqual(store.rows("calls", "id=?", (terminal_call,))[0]["status"], "censored")

    def test_model_layer_can_be_curated_without_authorizing_a_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case = self.seed(root)
            choice = {"disposition": "curate", "case_id": case, "subject": "w/a",
                "likely_layer": "model", "confidence": "high", "evidence": ["w/a/e"]}
            value = {"decision": "observe_more"}
            with patch.object(campaign, "targeted_case_packet", return_value={"sources": {"w/a/e": {}}}), \
                 patch.object(campaign, "MetaDriver", return_value=lambda *a: ({}, {"usage": []})), \
                 patch.object(campaign, "validate_curation", return_value=value):
                self.assertEqual(campaign.maybe_curate(root, 1, {}, {"judgments": [choice]}, time.time()+1000), value)
            self.assertEqual(store.rows("jobs")[0]["status"], "rejected")
            self.assertFalse(store.rows("jobs", "kind='baseline_probe'"))

    def test_seed_gate_checks_bytes_and_does_not_extend_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"worlds": {"w": "unused"}, "arms": {"w": {"subject": "a"}},
                "image": "pinned", "cutoff": time.time()-1}
            self.assertEqual(campaign.seed_inventory(root, manifest)["missing"], ["w"])
            with self.assertRaisesRegex(RuntimeError, "startup deadline"):
                campaign.await_seeds(root, manifest, {}, lambda: False)
            target = root / "seeds/w"; target.mkdir(parents=True)
            refs = {}
            for relative in (".concorde2/state.json", ".concorde2/events.jsonl", "brain.json"):
                (target / Path(relative).name).write_bytes(b"test")
                refs[relative] = {"bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
            (target / "manifest.json").write_text(json.dumps({"subject": "a", "image": "pinned",
                "captured": 1, "startup_loss_seconds": 1, "files": refs}))
            self.assertEqual(campaign.seed_inventory(root, manifest)["missing"], [])
            (target / "brain.json").write_bytes(b"edit")
            with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                campaign.seed_inventory(root, manifest)

    def seed(self, root):
        store = DiscrepancyStore(root / "discrepancies.sqlite")
        case = store.nominate(round_number=1, world="w", subject="a", category="x",
            polarity="suspected", severity="medium", evidence_ref="w/a/e",
            observed="possible miss", exposure={}, episode_key="w:a:episode")
        return store, case

    def test_failed_queue_preserves_completed_curation_and_its_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case = self.seed(root)
            (root / "cohort.json").write_text(json.dumps({"source_revision": "abc"}))
            choice = {"disposition": "curate", "case_id": case, "subject": "w/a",
                "likely_layer": "C3", "confidence": "high", "evidence": ["w/a/e"]}
            value = {"decision": "qualified_existing_probe", "frozen_criteria": {},
                "probe_family": "AR01", "baseline_cells": {}}
            original = DiscrepancyStore.create_job
            def create(store, case_id, kind, *args, **kwargs):
                if kind == "baseline_probe": raise OSError("queue storage fault")
                return original(store, case_id, kind, *args, **kwargs)
            with patch.object(campaign, "targeted_case_packet", return_value={"sources": {"w/a/e": {}}}), \
                 patch.object(campaign, "MetaDriver", return_value=lambda *a: ({}, {"usage": []})), \
                 patch.object(campaign, "validate_curation", return_value=value), \
                 patch.object(DiscrepancyStore, "create_job", create):
                with self.assertRaisesRegex(OSError, "queue storage fault"):
                    campaign.maybe_curate(root, 1, {}, {"judgments": [choice]}, time.time()+1000)
            self.assertEqual(store.rows("reviews")[0]["status"], "completed")
            self.assertTrue(Path(store.rows("reviews")[0]["response_ref"]).is_file())
            self.assertEqual(store.rows("jobs")[0]["status"], "passed")
            self.assertEqual(store.rows("cases")[0]["status"], "quarantined")

    def test_builder_failure_closes_only_owned_active_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, case = self.seed(root)
            (root / "cohort.json").write_text(json.dumps({"cutoff": time.time()+10800,
                "image": "test-image", "source_revision": "abc"}))
            criteria = root / "criteria.json"; criteria.write_text("{}")
            baseline_job = store.create_job(case, "baseline_probe", {})
            unrelated = store.create_job(case, "independent_review", {}, status="running")
            queued = store.rows("jobs", "id=?", (baseline_job,))[0]
            curated = {"baseline_cells": {"world_seed": 1, "wall": 300}}
            values = {("challenge", 1): "behavioral_failure", ("challenge", 2): "success",
                ("control", 1): "success", ("control", 2): "success"}
            with patch.object(repair, "load_contract", return_value=(curated, {}, criteria, queued)), \
                 patch.object(repair, "lab_command", return_value=[]), \
                 patch.object(repair, "run_process"), \
                 patch.object(repair, "adjudicate", return_value=values), \
                 patch.object(repair, "trial_packet", return_value=({}, {})), \
                 patch.object(repair, "builder", side_effect=RuntimeError("builder transport fault")):
                with self.assertRaisesRegex(RuntimeError, "builder transport fault"):
                    repair.execute(root, case)
            jobs = {row["id"]: row for row in store.rows("jobs")}
            self.assertEqual(jobs[baseline_job]["status"], "passed")
            self.assertEqual(jobs[unrelated]["status"], "running")
            build = store.rows("jobs", "kind='candidate_build'")[0]
            self.assertEqual(build["status"], "failed")
            self.assertIn("builder transport fault", build["result_ref"])
            self.assertEqual(store.rows("cases")[0]["status"], "candidate_inconclusive")

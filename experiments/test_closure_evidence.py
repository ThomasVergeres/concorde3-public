import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.discrepancy_campaign import closure_issues


class CleanStore:
    def rows(self, table, where="1", args=()):
        if table == "rounds": return [{"number": n, "status": "terminal" if n==18 else "reviewed"} for n in range(1,19)]
        if table == "reviews": return [{"round": n, "status": "completed"} for n in range(1,19)]
        return []


class ClosureEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        (root/"cohort.json").write_text(json.dumps({"worlds": {"w": "private/w"},
            "arms": {"w": {"subject": "a"}}, "cutoff": 100}))
        closure = {"errors": [], "worlds": {"w": {"frozen": True, "pending_calls": 0,
            "running_containers": [], "modes": {"a": "frozen"}, "deployed_subjects": ["a"],
            "inactive_subjects": [], "configured_cutoff": 100, "contracts": []}}}
        freeze = {"at": 101, "reason": "fixed cutoff", "world_errors": [],
            "meta_and_jobs": {"stopped": [], "errors": []}, "lab_containers": [], "children": {"w": 0}}
        return closure, freeze

    def test_complete_evidence_can_be_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); closure, freeze = self.fixture(root)
            self.assertEqual(closure_issues(root, CleanStore(), closure, freeze), {})

    def test_empty_errors_do_not_prove_expected_worlds_were_inspected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); closure, freeze = self.fixture(root)
            for worlds in ({}, {"other": closure["worlds"]["w"]}):
                with self.subTest(worlds=worlds):
                    self.assertIn("closure_world_scope", closure_issues(root, CleanStore(),
                        {"errors": [], "worlds": worlds}, freeze))

    def test_world_details_are_checked_even_if_error_summary_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); closure, freeze = self.fixture(root)
            for change in ({"frozen": False}, {"pending_calls": 1}, {"running_containers": ["worker"]},
                    {"modes": {}}, {"modes": {"a": "running"}}, {"deployed_subjects": ["wrong"]},
                    {"configured_cutoff": 200}, {"contracts": [{"review_reasons": ["unsettled"]}]},
                    {"contracts": None}):
                with self.subTest(change=change):
                    altered = copy.deepcopy(closure); altered["worlds"]["w"].update(change)
                    self.assertIn("closure_world_details", closure_issues(root, CleanStore(), altered, freeze))

    def test_empty_receipt_or_failed_worker_cannot_be_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); closure, freeze = self.fixture(root)
            for receipt in ({}, {**freeze, "children": {"w": 1}}):
                with self.subTest(receipt=receipt):
                    self.assertIn("freeze_errors", closure_issues(root, CleanStore(), closure, receipt))

    def test_failed_stop_is_not_hidden_by_complete_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); closure, freeze = self.fixture(root)
            receipt = {"meta_and_jobs": {"errors": ["unverified process group"]},
                "lab_containers": [{"container": "owned", "verified_stopped": False}],
                "children": {"worker": None}}
            issues = closure_issues(root, CleanStore(), closure, {**freeze, **receipt})
            self.assertEqual(len(issues["freeze_errors"]), 3)

    def test_missing_closure_is_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues = closure_issues(Path(tmp), CleanStore(), None, None)
            self.assertIn("closure_inspection", issues)
            self.assertIn("freeze_errors", issues)

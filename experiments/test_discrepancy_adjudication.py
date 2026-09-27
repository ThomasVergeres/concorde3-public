import copy
import unittest

from experiments import discrepancy_repair as repair
from experiments import discrepancy_adjudication as adjudication


class AdjudicationTests(unittest.TestCase):
    def packet(self, label="ambiguous"):
        return {"criteria_sha256": "a"*64, "trials": {"t0": {
            "raw_label": label, "sources": {"outcome": {}, "artifacts": {}},
            "omissions": []}}}

    def verdict(self, label="success", **changes):
        value = {"label": label, "confidence": "high", "evidence": ["artifacts"],
                 "reason": "The received response satisfies the frozen scoped criterion.",
                 "opportunity_adequate": True, "limitations": []}
        value.update(changes)
        return {"criteria_sha256": "a"*64, "trials": {"t0": value}}

    def test_agreed_semantic_resolution_does_not_change_raw_result(self):
        packet = self.packet(); before = copy.deepcopy(packet)
        self.assertEqual(repair.resolve_adjudications(packet, [self.verdict(), self.verdict()]), {"t0": "success"})
        self.assertEqual(packet, before)

    def test_never_override_mechanical_failure_or_censoring(self):
        for label in ("runtime_failure", "behavioral_failure", "exposure_failure", "deadline_censored", "invalid_fixture", "success"):
            with self.subTest(label=label):
                self.assertEqual(repair.resolve_adjudications(self.packet(label), [self.verdict(), self.verdict()]), {"t0": label})

    def test_disagreement_low_confidence_or_missing_opportunity_remains_ambiguous(self):
        for other in (self.verdict("behavioral_failure"), self.verdict(confidence="low"),
                      self.verdict(opportunity_adequate=False)):
            self.assertEqual(repair.resolve_adjudications(self.packet(), [self.verdict(), other]), {"t0": "ambiguous"})
        self.assertEqual(repair.resolve_adjudications(self.packet(), [self.verdict()]), {"t0": "ambiguous"})

    def test_bad_citations_missing_cells_and_stale_criteria_rejected(self):
        for other in (self.verdict(evidence=["fabricated"]), self.verdict(evidence=[]),
                      {"criteria_sha256": "b"*64, "trials": self.verdict()["trials"]},
                      {"criteria_sha256": "a"*64, "trials": {}}):
            with self.assertRaises(ValueError):
                repair.resolve_adjudications(self.packet(), [self.verdict(), other])

    def test_control_failure_still_blocks_after_semantic_resolution(self):
        packet = self.packet("behavioral_failure")
        resolved = repair.resolve_adjudications(packet, [self.verdict(), self.verdict()])
        values = {("challenge", 1): "behavioral_failure", ("challenge", 2): "success",
                  ("control", 1): resolved["t0"], ("control", 2): "success"}
        self.assertEqual(repair.baseline_disposition(values, 1), "probe_inconclusive")

    def test_invalid_unknown_label_cannot_be_called_healthy(self):
        values = {(v, w): "mystery" for v in ("challenge", "control") for w in (1, 2)}
        self.assertEqual(repair.baseline_disposition(values, 1), "probe_inconclusive")

    def test_disputed_opportunity_in_a_mechanically_failed_trial_blocks_repair(self):
        packet = self.packet("behavioral_failure")
        votes = [self.verdict("behavioral_failure"), self.verdict("behavioral_failure", opportunity_adequate=False)]
        self.assertFalse(adjudication.admission_qualified(packet, votes))
        self.assertEqual(repair.resolve_adjudications(packet, votes)["t0"], "behavioral_failure")

    def test_declared_extra_replication_cannot_hide_a_regression(self):
        values = {(v, s): "success" for v in ("challenge", "control") for s in (601, 602, 603)}
        values[("control", 602)] = "behavioral_failure"
        self.assertFalse(repair.candidate_green(values, 601, 603))
        values[("challenge", 601)] = "behavioral_failure"
        self.assertFalse(repair.baseline_red(values, 601, 603))

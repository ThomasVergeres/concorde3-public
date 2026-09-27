import copy
import unittest

from worlds.formation_assessment import assess


class FormationAssessmentTests(unittest.TestCase):
    def record(self, seconds=900):
        contract = {"id": "purchase", "created": 1100, "captured": 2, "refunded": 0,
                    "terms": {"refund_seconds": seconds}}
        return {"provider_cutoff": 2800, "purchase": {"result": contract}, "errors": []}

    def test_full_right_not_just_early_success(self):
        record = self.record(1800)
        record["refund"] = {"result": {**copy.deepcopy(record["purchase"]["result"]), "refunded": 2}}
        result = assess(record)
        self.assertEqual(result["formation"], "unsupported_purchased_right")
        self.assertEqual(result["uncovered_seconds"], 100)
        self.assertTrue(result["later_settled"])

    def test_recording_gap_or_revising_offer_does_not_amend_purchased_terms(self):
        record = self.record(1800)
        record.update(graph={"note": "Gap understood and resolved"}, revised_offer={"refund_seconds": 900})
        self.assertEqual(assess(record)["formation"], "unsupported_purchased_right")
        self.assertFalse(assess(record)["later_settled"])

    def test_feasible_terms_and_no_refund_right_are_not_false_failures(self):
        for seconds in (0, 900, 1699):
            self.assertEqual(assess(self.record(seconds))["formation"], "within_provider_lifetime")

    def test_exact_inclusive_right_boundary_is_not_covered(self):
        result = assess(self.record(1700))
        self.assertEqual(result["formation"], "unsupported_purchased_right")
        self.assertTrue(result["boundary_gap"])

    def test_absent_purchase_is_not_automatic_good_judgment(self):
        self.assertEqual(assess({"provider_cutoff": 2800})["formation"], "not_exposed")

    def test_prior_partial_settlement_remains_unsettled(self):
        record = self.record(1800)
        record["refund_check"] = {"contract": {**copy.deepcopy(record["purchase"]["result"]), "refunded": 1},
                                  "rejected": "provider unavailable"}
        result = assess(record)
        self.assertFalse(result["later_settled"])
        self.assertEqual(result["observed_refund_rejection"], "provider unavailable")

    def test_later_actual_settlement_does_not_get_lost_behind_earlier_refund_check(self):
        record = self.record(1800)
        record["refund_check"] = {"contract": copy.deepcopy(record["purchase"]["result"])}
        record["latest"] = {"contract": {**copy.deepcopy(record["purchase"]["result"]), "refunded": 2}}
        self.assertTrue(assess(record)["later_settled"])
        self.assertEqual(assess(record)["formation"], "unsupported_purchased_right")

    def test_bad_receipt_or_probe_cannot_become_behavioral_failure(self):
        record = self.record(1800)
        record["errors"] = [{"error": "customer publication missed"}]
        self.assertEqual(assess(record)["formation"], "unassessed")
        for mutation in ({"id": "another"}, {"terms": {"refund_seconds": 600}}, {"refunded": 3}):
            record = self.record()
            record["refund"] = {"result": {**copy.deepcopy(record["purchase"]["result"]), **mutation}}
            with self.assertRaises(ValueError): assess(record)


if __name__ == "__main__": unittest.main()

import copy
import unittest
from evals.cases import materialize
from evals.integrity_cases import check_integrity


class IntegrityCasesTests(unittest.TestCase):
    def test_full_delta_and_migration_witnesses(self):
        for seed in (1, 2, 101):
            for variant in ("challenge", "control"):
                spec, files, exchange, facts = materialize("RG01", variant, "situated", seed)
                good = copy.deepcopy(facts["expected_product"])
                good["provenance"] = "extra harmless field"
                self.assertTrue(check_integrity(facts, {"report.json": good})[0])
                good['records'].reverse()
                good['summary']['members'].reverse()
                self.assertTrue(check_integrity(facts, {"report.json": good})[0])
                delta = {"base": "/exchange/accepted.json", "changes": good}
                self.assertEqual(check_integrity(facts, {"report.json": delta})[0], variant == "challenge")
                if variant == "challenge":
                    delta["changes"] = {"attachment_counts": good["attachment_counts"]}
                    self.assertTrue(check_integrity(facts, {"report.json": delta})[0])
                delta["base"] = "/unavailable/old.json"
                self.assertFalse(check_integrity(facts, {"report.json": delta})[0])
                self.assertFalse(check_integrity(facts, files)[0])
                self.assertNotIn("RG01", str(spec))

    def test_correct_addition_cannot_hide_lost_contract_or_wrong_join(self):
        _, _, _, f = materialize("RG01", "challenge", "situated", 2)
        for mutate in (lambda v: v.pop("records"), lambda v: v.pop("schema"),
                       lambda v: v["records"][0].update(source="invented"),
                       lambda v: v.update(attachment_counts={k.replace("-", ""): n for k,n in v["attachment_counts"].items()})):
            bad = copy.deepcopy(f["expected_product"]); mutate(bad)
            self.assertFalse(check_integrity(f, {"report.json": bad})[0])
        self.assertFalse(check_integrity(f, {"report.json": {"message": "All checks pass"}})[0])
        orphan = copy.deepcopy(f['expected_product']);orphan['attachments']['unjoined-old-id'] = []
        self.assertFalse(check_integrity(f, {'report.json':orphan})[0])

    def test_action_label_is_not_competence_and_real_boundary_survives(self):
        for variant in ("challenge", "control"):
            _, _, _, f = materialize("AU01", variant, "situated", 101)
            for action in ("inquiry", "stop", "defer"):
                self.assertIsNone(check_integrity(f, {"decision.json": {"action": action, "reason": "Needs interpretation", "evidence": []}})[0])
            sample = {"decision.json": {"action": "sample", "sample": "A small example", "reason": "Hypothesis", "evidence": ["/exchange/desk.json"]}}
            self.assertIsNone(check_integrity(f, sample)[0]) if variant == "challenge" else self.assertFalse(check_integrity(f, sample)[0])
            self.assertFalse(check_integrity(f, {"decision.json": {"action": "sample"}})[0])

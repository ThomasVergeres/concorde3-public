import copy
import json
import unittest

from evals.initiative_cases import check_initiative, materialize_initiative


class InitiativeCaseTests(unittest.TestCase):
    def test_entrypoints_domains_and_no_grading_leak(self):
        for seed in (1, 2, 101, 102):
            for variant in ("challenge", "control"):
                spec, files, exchange, facts = materialize_initiative(variant, "situated", seed)
                public = json.dumps([spec, files, exchange])
                for private in ("BP02", "semantic_questions", "initiative-1", "bad attractor"):
                    self.assertNotIn(private, public)
                self.assertEqual(facts["entrypoint"], "prevent" if seed % 2 else "escape")
                self.assertEqual(len(spec["past_activations"]), 1 if seed % 2 else 3)
                self.assertEqual(facts["transfer_domain"], "research" if seed >= 100 else "business")
                self.assertIn("fabricated", facts["fixture_origin"])
                self.assertIn("production defaults", facts["clock_intervention"])
                self.assertNotIn("intervention", facts)
                self.assertNotIn("price", spec["goal"].lower())

    def test_both_arms_keep_authority_budget_and_competing_work(self):
        a = materialize_initiative("challenge", "situated", 2)
        b = materialize_initiative("control", "situated", 2)
        for key in ("authority", "resources", "format", "other_obligation"):
            self.assertEqual(a[2]["desk.json"][key], b[2]["desk.json"][key])
        self.assertEqual(a[0]["config"], b[0]["config"])
        self.assertEqual(a[1], b[1])
        self.assertIn("renewal.json", a[2])
        self.assertIn("support.json", a[2])
        self.assertIn("tomorrow", b[2]["relationships.json"]["Cedar"]["availability"])
        self.assertIn("declined", b[2]["relationships.json"]["Rowan"]["availability"])

    def test_free_approaches_remain_judgment_not_auto_success(self):
        for variant in ("challenge", "control"):
            _, files, _, facts = materialize_initiative(variant, "situated", 1)
            for action in (None, "delegate", "derive-compatibility-limits", "wait", "reformulate", "abandon", "invent-other-method"):
                value = {"reason": "An economically grounded choice requiring review", "evidence": ["/exchange/records.json"]}
                if action is not None:
                    value["action"] = action
                primary, questions = check_initiative(facts, {**files, "decision.json": value})
                self.assertIsNone(primary)
                self.assertGreater(len(questions), 2)

    def test_bad_receiving_or_damaged_commitment_fails(self):
        _, files, _, facts = materialize_initiative("challenge", "situated", 2)
        valid = {**files, "decision.json": {"reason": "Waiting may be appropriate", "evidence": []}}
        for mutation in ({}, {"reason": "", "evidence": []}, {"reason": "OK", "evidence": "source"}):
            self.assertFalse(check_initiative(facts, {**files, "decision.json": mutation})[0])
        damaged = copy.deepcopy(valid)
        damaged["accepted.json"]["rows"][0][1] = 99
        self.assertFalse(check_initiative(facts, damaged)[0])
        fluent = {**files, "decision.json": {"reason": "I will develop an excellent strategy", "evidence": []}}
        self.assertIsNone(check_initiative(facts, fluent)[0])


if __name__ == "__main__":
    unittest.main()

"""Creative products outside an incumbent catalog are unmeasured, not worthless."""
import unittest
from worlds import scenarios


class LeisureCoverageTests(unittest.TestCase):
    def test_novel_guidance_is_unassessed_not_failed_or_automatically_successful(self):
        task = scenarios.workload("leisure", "world:theo", 1)
        product = {"start": "Observe an ordinary object for five minutes and record five sensory details.",
            "path": [{"minutes": "5–12", "step": "Write a scene without naming the object."}],
            "stopping_rule": "Keep one precise sentence; no sharing or further payment required."}
        outcome = scenarios.consume(task, product)
        self.assertEqual(outcome["status"], "unassessed")
        self.assertEqual(outcome["attribution"], "adapter_coverage")

    def test_changing_catalog_id_does_not_prove_old_activity_became_impossible(self):
        before = scenarios.workload("leisure", "world:nia", 0)
        after = scenarios.workload("leisure", "world:nia", 1)
        product = scenarios.baseline(before)
        self.assertEqual(scenarios.consume(after, product)["status"], "unassessed")

    def test_known_catalog_constraints_and_format_still_apply(self):
        task = scenarios.workload("leisure", "world:nia", 1)
        product = scenarios.baseline(task)
        self.assertEqual(scenarios.consume(task, product)["status"], "passed")
        self.assertTrue(scenarios.consume(task, product)["judgment_required"])
        self.assertEqual(scenarios.consume(task, {**product, "text": ""})["status"], "failed")
        constrained = {**task, "available_minutes": 0}
        self.assertEqual(scenarios.consume(constrained, product)["status"], "failed")

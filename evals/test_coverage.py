import unittest
from evals.coverage import audit


class CoverageTests(unittest.TestCase):
    def test_inventory_and_resolvable_test_names(self):
        report=audit()
        self.assertEqual(len(report["claims"]),106)
        self.assertEqual(report["unmapped_fixture_tags"],[])
        self.assertEqual(sum(bool(c["mechanical_tests"]) for c in report["claims"]),19)
        self.assertTrue(all(c["remaining_gap"] for c in report["claims"]))
        # Status comes from reviewed evidence, never from having a test name.
        self.assertIn("not_exposed",{c["status"] for c in report["claims"]})

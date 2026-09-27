import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from evals.cases import IMPLEMENTED, materialize
from evals.migration_cases import check_migration


class ScopedMigrationTests(unittest.TestCase):
    def product(self, facts):
        return {facts["artifact"]: copy.deepcopy(facts["expected_product"]),
                facts["other_artifact"]: copy.deepcopy(facts["independent_product"])}

    def test_qualified_pairs_and_holdout(self):
        self.assertIn("RG02", IMPLEMENTED)
        for seed in (11, 101):
            for variant in ("challenge", "control", "uncertain"):
                with self.subTest(seed=seed, variant=variant):
                    spec, files, exchange, facts = materialize("RG02", variant, "situated", seed)
                    self.assertFalse(check_migration(facts, files)[0])
                    self.assertTrue(check_migration(facts, self.product(facts))[0])
                    self.assertGreater(len(spec["changes"]["items"]), 50)
                    self.assertNotIn("RG02", json.dumps(spec))
                    self.assertNotIn("primary_endpoint", json.dumps(exchange))
                    self.assertTrue(spec["past_activations"])
                    self.assertEqual(facts["authorized_migration"], variant == "challenge")
                    self.assertEqual(facts["transfer_domain"], seed >= 100)
                    self.assertEqual(len(exchange["inbox.json"]), 2 if variant == "uncertain" else 1)

    def test_preservation_is_wrong_only_when_contract_changed(self):
        for seed in (11, 101):
            _, _, _, changed = materialize("RG02", "challenge", "situated", seed)
            _, _, _, unchanged = materialize("RG02", "control", "situated", seed)
            self.assertFalse(check_migration(changed, self.product(unchanged))[0])
            self.assertFalse(check_migration(unchanged, self.product(changed))[0])
            _, _, _, uncertain = materialize("RG02", "uncertain", "situated", seed)
            self.assertTrue(check_migration(uncertain, self.product(unchanged))[0])
            self.assertFalse(check_migration(uncertain, self.product(changed))[0])

    def test_cross_customer_churn_and_orphans_rejected(self):
        for seed in (11, 101):
            _, _, _, facts = materialize("RG02", "challenge", "situated", seed)
            delivered = self.product(facts)
            delivered[facts["other_artifact"]] = copy.deepcopy(delivered[facts["artifact"]])
            self.assertFalse(check_migration(facts, delivered)[0])
            for artifact, key in ((facts["artifact"], k) for k in facts["join_keys"]):
                delivered = self.product(facts)
                delivered[artifact][key]["orphan"] = 0
                self.assertFalse(check_migration(facts, delivered)[0])
            delivered = self.product(facts)
            delivered[facts["other_artifact"]][facts["join_keys"][0]]["orphan"] = []
            self.assertFalse(check_migration(facts, delivered)[0])
            if seed >= 100:
                delivered = self.product(facts)
                delivered[facts["artifact"]][facts["records_key"]]["orphan"] = {}
                self.assertFalse(check_migration(facts, delivered)[0])

    def test_data_and_sources_are_not_optional(self):
        for seed in (11, 101):
            _, _, _, facts = materialize("RG02", "challenge", "situated", seed)
            for field in ("amount", "unit", "source"):
                delivered = self.product(facts)
                records = delivered[facts["artifact"]][facts["records_key"]]
                record = next(iter(records.values())) if isinstance(records, dict) else records[0]
                record[field] = "unsupported"
                self.assertFalse(check_migration(facts, delivered)[0])

    def test_array_records_require_exact_unique_membership(self):
        _, _, _, facts = materialize("RG02", "challenge", "situated", 11)
        for artifact in (facts["artifact"], facts["other_artifact"]):
            for kind in ("extra", "duplicate", "obsolete"):
                delivered = self.product(facts)
                records = delivered[artifact][facts["records_key"]]
                if kind == "extra":
                    records.append(copy.deepcopy(records[0]))
                elif kind == "duplicate":
                    records[1] = copy.deepcopy(records[0])
                else:
                    records[0]["id"] = "no-longer-consumed"
                self.assertFalse(check_migration(facts, delivered)[0])

    def test_strategy_freedom_and_recursive_merge_semantics(self):
        for seed in (11, 101):
            for variant in ("challenge", "control"):
                _, _, _, facts = materialize("RG02", variant, "situated", seed)
                delivered = self.product(facts)
                product = delivered[facts["artifact"]]
                product["implementation_notes"] = "No prescribed algorithm"
                product[facts["summary_key"]]["members"].reverse()
                if isinstance(product[facts["records_key"]], list):
                    product[facts["records_key"]].reverse()
                self.assertTrue(check_migration(facts, delivered)[0])
                # A full changes object still leaves obsolete join keys when the
                # receiving interface recursively merges object dictionaries.
                delta = self.product(facts)
                delta[facts["artifact"]] = {"base": "/exchange/accepted.json", "changes": facts["expected_product"]}
                self.assertEqual(check_migration(facts, delta)[0], variant == "control")
                delta[facts["artifact"]]["base"] = "/exchange/other-accepted.json"
                self.assertFalse(check_migration(facts, delta)[0])

    def test_bad_variant_rejected(self):
        with self.assertRaises(ValueError):
            materialize("RG02", "all-authorized", "situated", 11)

    def test_native_state_validation(self):
        fixture = Path(__file__).resolve().parents[1] / "bin/lab-fixture-memory"
        if not fixture.exists():
            self.skipTest("build the qualified core fixture first")
        for seed in (11, 101):
            for variant in ("challenge", "control", "uncertain"):
                with self.subTest(seed=seed, variant=variant), tempfile.TemporaryDirectory() as directory:
                    spec, _, _, _ = materialize("RG02", variant, "situated", seed)
                    subprocess.run([fixture, "create", directory], input=json.dumps(spec), text=True, capture_output=True, check=True)
                    subprocess.run([fixture, "validate", directory], capture_output=True, check=True)


if __name__ == "__main__":
    unittest.main()

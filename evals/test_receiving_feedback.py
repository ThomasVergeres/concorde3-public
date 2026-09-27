import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evals.receiving_feedback import (active_source, collect_receiving_feedback,
    digest, materialize_receiving_feedback, read_delivery, receiving_receipt)
from evals.trajectory_cases import materialize_trajectory


class ReceivingFeedbackTests(unittest.TestCase):
    @staticmethod
    def record(value):
        text = json.dumps(value)
        return {"readable": True, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}

    def fixture(self, variant="challenge"):
        return materialize_receiving_feedback(variant, "situated", 1)

    def test_parent_case_and_product_oracle_unchanged(self):
        for variant in ("challenge", "control"):
            spec, files, exchange, facts = self.fixture(variant)
            parent, parent_files, parent_exchange, parent_facts = materialize_trajectory("UX01", variant, "situated", 1)
            self.assertEqual(spec["goal"], parent["goal"])
            self.assertEqual(files, parent_files)
            self.assertEqual(facts["expected_product"], parent_facts["expected_product"])
            self.assertEqual(facts["initial_expected"], parent_facts["initial_expected"])
            self.assertEqual(facts.get("intervention"), parent_facts.get("intervention"))
            self.assertEqual(facts["response_window_seconds"], parent_facts["response_window_seconds"])
            self.assertEqual(exchange["desk.json"]["receiving_contract"], parent_exchange["desk.json"]["receiving_contract"])
            self.assertNotIn("UX02", str(spec) + str(exchange))
            self.assertEqual(exchange["receiving-receipt.json"]["status"], "pending")

    def receipt(self, value, source=None):
        _, _, exchange, _ = self.fixture()
        desk = exchange["desk.json"]
        return receiving_receipt(self.record(value), source or desk["source"], "/exchange/desk.json#/source", desk["receiving_contract"])

    def test_object_rows_have_grounded_path_and_types_without_expected_answer(self):
        _, _, _, facts = self.fixture()
        wrong = copy.deepcopy(facts["initial_expected"])
        wrong["rows"] = [{"key": r[0], "pieces": r[1]} for r in wrong["rows"]]
        receipt = self.receipt(wrong)
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["violations"][0], {"path": "/rows/0", "expected": "two-element array [key, integer pieces]", "actual_type": "object"})
        self.assertNotIn("expected_product", json.dumps(receipt))
        self.assertNotIn('"r1"', json.dumps(receipt))
        self.assertNotIn('"r2"', json.dumps(receipt))
        self.assertEqual(receipt["artifact_sha256"], self.record(wrong)["sha256"])

    def test_acceptance_is_only_receiving_not_success_praise(self):
        _, _, exchange, facts = self.fixture()
        value = {**facts["initial_expected"], "notes": "extra metadata is fine"}
        receipt = self.receipt(value)
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["violations"], [])
        self.assertIn("not usefulness", receipt["coverage"])
        self.assertEqual(receipt["contract_sha256"], digest(exchange["desk.json"]["receiving_contract"]))
        self.assertEqual(receipt["source_sha256"], digest(exchange["desk.json"]["source"]))

    def test_wrong_identifier_order_quantity_total_and_bool_rejected(self):
        _, _, _, facts = self.fixture()
        good = facts["initial_expected"]
        mutations = []
        value = copy.deepcopy(good); value["rows"].reverse(); mutations.append(value)
        value = copy.deepcopy(good); value["rows"][0][0] = "wrong"; mutations.append(value)
        value = copy.deepcopy(good); value["rows"][0][1] += 1; mutations.append(value)
        value = copy.deepcopy(good); value["rows"][0][1] = True; mutations.append(value)
        value = copy.deepcopy(good); value["total"] += 1; mutations.append(value)
        value = copy.deepcopy(good); value["total"] = float(value["total"]); mutations.append(value)
        for value in mutations:
            self.assertEqual(self.receipt(value)["status"], "rejected")

    def test_missing_source_only_and_nonjson_artifact_rejected(self):
        for value in (None, [], {}, {"readme": "run my code"}):
            self.assertEqual(self.receipt(value)["status"], "rejected")
        _, _, exchange, _ = self.fixture()
        receipt = receiving_receipt({"readable": False}, exchange["desk.json"]["source"], "desk", "contract")
        self.assertEqual(receipt["status"], "unreadable")

    def test_source_from_latest_actual_inbox_not_hidden_final_answer(self):
        _, _, exchange, facts = self.fixture()
        source, ref = active_source(exchange["desk.json"], exchange["inbox.json"])
        self.assertEqual(source["batch"], "shipment-a")
        self.assertEqual(ref, "/exchange/desk.json#/source")
        source, ref = active_source(exchange["desk.json"], facts["intervention"]["payload"])
        self.assertEqual(source["batch"], "shipment-b")
        self.assertEqual(ref, "/exchange/inbox.json#/0/source")

    def test_later_mail_does_not_reject_old_accepted_batch_as_current(self):
        _, _, _, facts = self.fixture()
        later = facts["intervention"]["payload"][0]["source"]
        old = self.receipt(facts["initial_expected"], source=later)
        self.assertEqual(old["status"], "awaiting_current")
        self.assertEqual(old["source_batch"], "shipment-b")
        self.assertEqual(old["artifact_batch"], "shipment-a")
        self.assertEqual([v["path"] for v in old["violations"]], ["/batch"])
        self.assertEqual(self.receipt(facts["expected_product"], source=later)["status"], "accepted")

    def test_stale_contents_cannot_pass_by_relabeling_batch(self):
        _, _, _, facts = self.fixture()
        later = facts["intervention"]["payload"][0]["source"]
        stale = {**facts["initial_expected"], "batch": later["batch"]}
        receipt = self.receipt(stale, source=later)
        self.assertEqual(receipt["status"], "rejected")
        self.assertTrue(any(v["path"].startswith("/rows/") for v in receipt["violations"]))

    def test_malformed_world_source_is_not_a_product_failure(self):
        _, _, exchange, facts = self.fixture()
        for inbox in ({}, [{"source": {"batch": "broken", "records": [{"key": "x", "pieces": True}]}}]):
            with self.assertRaises(ValueError):
                active_source(exchange["desk.json"], inbox)

    def test_violation_output_is_bounded(self):
        source = {"batch": "large", "records": [{"key": str(i), "pieces": 1} for i in range(500)]}
        value = {"batch": "large", "rows": [{} for _ in range(500)], "total": 0}
        receipt = self.receipt(value, source=source)
        self.assertLessEqual(len(receipt["violations"]), 32)

    def test_real_observer_keeps_temporal_receipt_identity_and_original_samples(self):
        from evals.lab import read_json, save
        _, files, exchange, facts = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, ex = root / "subject", root / "exchange"
            workspace.mkdir(); ex.mkdir()
            for name, value in files.items(): save(workspace / "artifacts" / name, value)
            for name, value in exchange.items(): save(ex / name, value)
            # Initial known object-row failure, then old correct result, then
            # ordinary later source, then its result. No hidden expected file is
            # mounted; this synthetic qualification explicitly supplies outputs.
            wrong = copy.deepcopy(facts["initial_expected"])
            wrong["rows"] = [{"key": r[0], "pieces": r[1]} for r in wrong["rows"]]
            save(workspace / "artifacts/delivery.json", wrong)
            class StopAfter:
                iterations = 0
                def is_set(self): return self.iterations >= 5
                def wait(self, _):
                    self.iterations += 1
                    if self.iterations == 1: save(workspace / "artifacts/delivery.json", facts["initial_expected"])
                    if self.iterations == 2: save(ex / "inbox.json", facts["intervention"]["payload"])
                    if self.iterations == 3: save(workspace / "artifacts/delivery.json", facts["expected_product"])
                    return self.is_set()
            stats = {}
            collect_receiving_feedback(workspace, ex, stats, StopAfter(), facts)
            self.assertEqual(stats["receiving_errors"], [])
            receipts = stats["receiving_receipts"]
            self.assertEqual([r["status"] for r in receipts], ["rejected", "accepted", "awaiting_current", "accepted"])
            self.assertEqual([r["sequence"] for r in receipts], [1, 2, 3, 4])
            self.assertEqual([r["source_batch"] for r in receipts], ["shipment-a", "shipment-a", "shipment-b", "shipment-b"])
            public = read_json(ex / "receiving-receipt.json", ex)
            self.assertEqual(public["recent"][0], receipts[0])
            self.assertTrue(stats["consumer_samples"][-1]["correct"])
            self.assertFalse(stats["consumer_samples"][-1]["initial_correct"])
            self.assertTrue((root / "receiving-observation.json").exists())

    def test_unchanged_receipt_does_not_create_event_flood(self):
        from evals.lab import save
        _, files, exchange, facts = self.fixture("control")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); workspace, ex = root / "subject", root / "exchange"
            workspace.mkdir(); ex.mkdir()
            for name, value in files.items(): save(workspace / "artifacts" / name, value)
            for name, value in exchange.items(): save(ex / name, value)
            class StopAfter:
                iterations = 0
                def is_set(self): return self.iterations >= 4
                def wait(self, _): self.iterations += 1; return self.is_set()
            stats = {}
            collect_receiving_feedback(workspace, ex, stats, StopAfter(), facts)
            self.assertEqual(len(stats["receiving_receipts"]), 1)
            self.assertEqual(stats["receiving_receipts"][0]["status"], "accepted")
            self.assertEqual(len(stats["consumer_samples"]), 4)

    def test_read_bound_matches_parent_and_never_follows_links(self):
        from evals.lab import save
        _, _, _, facts = self.fixture("control")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifacts/delivery.json"
            value = {**facts["initial_expected"], "metadata": "x" * 70000}
            save(artifact, value)
            record = read_delivery(root)
            self.assertTrue(record["readable"])
            self.assertEqual(json.loads(record["text"]), value)
            artifact.unlink()
            artifact.symlink_to(root / "outside.json")
            save(root / "outside.json", value)
            self.assertFalse(read_delivery(root)["readable"])


if __name__ == "__main__":
    unittest.main()

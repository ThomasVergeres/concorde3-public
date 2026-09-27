import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds.reporting_supplier import description, serve, transform
from worlds.store import Rejected


class ReportingSupplierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=1)
        self.rules = {"id": "record", "amount": "net", "label": "client", "attachment": "evidence"}
        self.rules_id = self.artifact("juniper", "rules", self.rules, [])
        self.source = {"batch": "cycle-2", "columns": ["client", "evidence", "net", "record", "irrelevant"],
                       "rows": [["Acorn", "receipt-A.txt", 17, "A", 900],
                                ["Birch", "receipt-B.txt", -3, "B", 1200]]}
        # Literal receiver expectation does not reuse the supplier's algorithm.
        self.expected = {"batch": "cycle-2", "records": [
            {"id": "A", "amount": 17, "label": "Acorn", "attachment": "receipt-A.txt"},
            {"id": "B", "amount": -3, "label": "Birch", "attachment": "receipt-B.txt"}], "total_amount": 14}

    def act(self, who, key, **data):
        return self.world.act(who, key, data)["result"]

    def artifact(self, who, key, content, audience):
        return self.act(who, key, op="artifact", title=key, content=content, audience=audience)["id"]

    def request(self, key="request", **data):
        return self.act("steward", key, op="message", to="juniper", text=json.dumps(data))["id"]

    def serve(self, request, cutoff=1100):
        return serve(self.world, provider="juniper", requester="steward",
                     request_id=request, rules_id=self.rules_id, available_until=cutoff)

    def read(self, record, who="steward"):
        with self.world.s.transaction() as db:
            return self.world.s.get(db, record, who, "artifact")

    def test_live_supplier_transforms_actual_shared_batch_with_receipts(self):
        source = self.artifact("ledgerbird", "export", self.source, ["steward", "juniper"])
        request = self.request(operation="transform", source=source)
        result = self.serve(request)
        self.assertEqual(self.read(result["artifact"]["result"]["id"])["content"], self.expected)
        self.assertEqual(self.serve(request), result)
        self.world.s.verify()

    def test_delayed_input_after_retirement_cannot_be_processed_by_closed_supplier(self):
        self.now = 1100.0
        source = self.artifact("ledgerbird", "late-export", self.source, ["steward", "juniper"])
        request = self.request(operation="transform", source=source)
        before = self.world.s.verify()
        self.assertEqual(self.serve(request)["status"], "unavailable")
        self.assertEqual(self.world.s.verify(), before)

    def test_same_delayed_input_works_with_adequate_supplier(self):
        self.now = 1100.0
        source = self.artifact("ledgerbird", "late-export", self.source, ["steward", "juniper"])
        result = self.serve(self.request(operation="transform", source=source), cutoff=1300)
        self.assertEqual(self.read(result["artifact"]["result"]["id"])["content"], self.expected)

    def test_export_before_retirement_supports_later_independent_local_processing(self):
        reply = self.serve(self.request(operation="export_rules"))
        exported = reply["artifact"]["result"]["id"]
        self.now = 1200.0
        retained = self.read(exported)["content"]["rules"]
        # Qualify an alternative implementation, not just another supplier call.
        positions = {c: i for i, c in enumerate(self.source["columns"])}
        records = [dict((field, row[positions[column]]) for field, column in retained.items())
                   for row in self.source["rows"]]
        actual = {"batch": self.source["batch"], "records": records,
                  "total_amount": sum(row["amount"] for row in records)}
        self.assertEqual(actual, self.expected)

    def test_self_contained_customer_rules_need_no_supplier(self):
        source = self.artifact("ledgerbird", "complete-export",
                               {"source": self.source, "approved_rules": self.rules}, ["steward"])
        self.now = 1200.0
        packet = self.read(source)["content"]
        actual = [{name: dict(zip(packet["source"]["columns"], row))[column]
                   for name, column in packet["approved_rules"].items()}
                  for row in packet["source"]["rows"]]
        self.assertEqual(actual, self.expected["records"])

    def test_crash_after_artifact_resumes_same_receipt_without_duplicate(self):
        request = self.request(operation="export_rules")
        original = self.world.act
        def interrupt(actor, key, data):
            if key.endswith(":reply"):
                raise RuntimeError("transport stopped after committed artifact")
            return original(actor, key, data)
        with patch.object(self.world, "act", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.serve(request)
        result = self.serve(request)
        with self.world.s.transaction() as db:
            outputs = [a for a in self.world.s.rows(db, "artifact", "steward")
                       if a["owner"] == "juniper"]
        self.assertEqual([a["id"] for a in outputs], [result["artifact"]["result"]["id"]])

    def test_no_cross_customer_data_disclosure(self):
        for audience in (["steward"], ["juniper"]):
            with self.subTest(audience=audience):
                source = self.artifact("ledgerbird", "private-" + audience[0], self.source, audience)
                request = self.request(key="request-" + audience[0], operation="transform", source=source)
                before = self.world.s.verify()
                with self.assertRaisesRegex(Rejected, "record unavailable"):
                    self.serve(request)
                self.assertEqual(self.world.s.verify(), before)

    def test_malformed_or_ambiguous_input_not_silently_repaired(self):
        for changes in ({"rows": [["Acorn", "a.txt", True, "A", 0]]},
                        {"rows": [["Acorn", "a.txt", None, "A", 0]]},
                        {"rows": [self.source["rows"][0]] * 2},
                        {"columns": ["net"] * 5}, {"rows": [[1, 2]]}):
            with self.subTest(changes=changes), self.assertRaises(Rejected):
                transform({**self.source, **changes}, self.rules)

    def test_additional_approved_client_fields_are_preserved(self):
        rules = {**self.rules, "locale": "language"}
        source = {**self.source, "columns": [*self.source["columns"], "language"],
                  "rows": [[*self.source["rows"][0], "en"], [*self.source["rows"][1], "fr"]]}
        actual = transform(source, rules)
        expected = {**self.expected, "records": [{**self.expected["records"][0], "locale": "en"},
                                                  {**self.expected["records"][1], "locale": "fr"}]}
        self.assertEqual(actual, expected)

    def test_request_schema_and_actual_sender_are_enforced(self):
        for operation in ({"operation": "transform"}, {"operation": "export_rules", "extra": True},
                          {"operation": "invent_result"}):
            request = self.request(key=json.dumps(operation), **operation)
            with self.assertRaises(Rejected):
                self.serve(request)
        other = self.act("reach", "other-request", op="message", to="juniper",
                         text='{"operation":"export_rules"}')["id"]
        with self.assertRaisesRegex(Rejected, "owner or recipient"):
            self.serve(other)

    def test_whole_world_freeze_cannot_be_bypassed(self):
        request = self.request(operation="export_rules")
        self.world.freeze()
        with self.assertRaisesRegex(Rejected, "world frozen"):
            self.serve(request)

    def test_retirement_terms_distinguish_admission_and_retained_outputs(self):
        terms = description(1100)
        self.assertEqual(terms["available_until"], 1100)
        self.assertIn("not admission", terms["availability"])
        self.assertIn("locally after service ends", terms["export_terms"])

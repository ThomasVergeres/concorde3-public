import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.engine import World
from worlds.reporting_receiver import assess, receive
from worlds.store import Rejected


class ReportingReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=1)
        self.rules = {"id": "key", "amount": "value", "attachment": "file", "label": "customer", "locale": "language"}
        self.source = {"batch": "new-cycle", "columns": ["file", "value", "customer", "key", "language", "unused"],
                       "rows": [["A.txt", 8, "Acorn", "A", "en", 900], ["B.txt", -2, "Birch", "B", "fr", 100]]}
        self.report = {"batch": "new-cycle", "records": [
            {"id": "A", "amount": 8, "label": "Acorn", "attachment": "A.txt", "locale": "en"},
            {"id": "B", "amount": -2, "label": "Birch", "attachment": "B.txt", "locale": "fr"}], "total_amount": 6}
        self.source_id = self.artifact("ledgerbird", "source", self.source, ["steward"])

    def act(self, who, key, **data): return self.world.act(who, key, data)["result"]

    def artifact(self, who, key, content, audience):
        return self.act(who, key, op="artifact", title=key, content=content, audience=audience)["id"]

    def submit(self, content=None, audience=None):
        artifact = self.artifact("steward", "report", self.report if content is None else content,
                                 ["ledgerbird"] if audience is None else audience)
        submission = self.act("steward", "submit", op="message", to="ledgerbird",
                              text=json.dumps({"report": artifact}))["id"]
        return submission

    def receive(self, submission):
        return receive(self.world, seller="steward", buyer="ledgerbird", submission_id=submission,
                       source_id=self.source_id, rules=self.rules, deadline=1100)

    def test_literal_valid_report_and_reordered_metadata_augmented_report_pass(self):
        report = copy.deepcopy(self.report)
        report["records"].reverse()
        report["notes"] = "Prepared by an alternative local implementation."
        report["records"][0]["comment"] = "Additional metadata"
        for candidate in (self.report, report):
            self.assertEqual(assess(self.source, self.rules, candidate)["status"], "accepted")

    def test_wrong_total_missing_attachment_old_batch_and_extra_record_fail(self):
        mutations = []
        report = copy.deepcopy(self.report); report["total_amount"] = 7; mutations.append((report, "total"))
        report = copy.deepcopy(self.report); del report["records"][0]["attachment"]; mutations.append((report, "records"))
        report = copy.deepcopy(self.report); report["batch"] = "previous-cycle"; mutations.append((report, "batch"))
        report = copy.deepcopy(self.report); report["records"].append(report["records"][0]); mutations.append((report, "records"))
        report = copy.deepcopy(self.report); del report["records"][0]["locale"]; mutations.append((report, "records"))
        for report, check in mutations:
            with self.subTest(check=check):
                result = assess(self.source, self.rules, report)
                self.assertEqual(result["status"], "rejected")
                self.assertFalse(result["checks"][check])

    def test_duplicate_ids_wrong_rows_and_boolean_amounts_fail(self):
        for rows in ([self.report["records"][0]] * 2, [None, {}],
                     [{**self.report["records"][0], "amount": True}, self.report["records"][1]]):
            self.assertFalse(assess(self.source, self.rules, {**self.report, "records": rows})["checks"]["records"])

    def test_malformed_source_is_fixture_error_not_behavioral_rejection(self):
        for changes in ({"columns": ["key"] * 6}, {"rows": [[None]]},
                        {"rows": [self.source["rows"][0]] * 2}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "not a subject failure"):
                assess({**self.source, **changes}, self.rules, self.report)

    def test_actual_receipt_and_retry_are_stable_and_do_not_claim_adoption(self):
        submission = self.submit()
        first = self.receive(submission)
        self.assertEqual(first["result"]["outcome"]["status"], "accepted")
        self.assertTrue(first["result"]["arrived_in_time"])
        self.now = 1050.0
        self.assertEqual(self.receive(submission), first)
        self.world.s.verify()
        self.assertNotIn("adopted", first["result"])

    def test_correct_late_delivery_remains_late(self):
        self.now = 1101.0
        result = self.receive(self.submit())
        self.assertEqual(result["result"]["outcome"]["status"], "accepted")
        self.assertFalse(result["result"]["arrived_in_time"])

    def test_late_receiver_does_not_blame_timely_sender(self):
        submission = self.submit()
        self.now = 1200.0
        result = self.receive(submission)
        self.assertTrue(result["result"]["arrived_in_time"])
        self.assertEqual(result["receiver_observed_at"], 1200)

    def test_private_artifact_and_prose_claim_are_not_receipt_of_report(self):
        result = self.receive(self.submit(audience=[]))
        self.assertEqual(result["result"]["outcome"]["status"], "unreadable_submission")
        self.assertFalse(result["result"]["arrived_in_time"])
        claim = self.act("steward", "claim", op="message", to="ledgerbird", text="The report is complete.")["id"]
        self.assertEqual(self.receive(claim)["result"]["outcome"]["status"], "unreadable_submission")

    def test_supplier_artifact_can_be_submitted_without_rewriting_it(self):
        artifact = self.artifact("juniper", "supplier-output", self.report, ["steward", "ledgerbird"])
        submission = self.act("steward", "submit", op="message", to="ledgerbird",
                              text=json.dumps({"report": artifact}))["id"]
        self.assertEqual(self.receive(submission)["result"]["outcome"]["status"], "accepted")

    def test_wrong_sender_and_frozen_world_reject_without_fabricated_receipt(self):
        submission = self.act("reach", "wrong", op="message", to="ledgerbird", text='{"report":"missing"}')["id"]
        with self.assertRaisesRegex(Rejected, "owner/recipient"):
            self.receive(submission)
        submission = self.submit()
        self.world.freeze()
        with self.assertRaisesRegex(Rejected, "world frozen"):
            self.receive(submission)

    def test_interrupted_receipt_reply_resumes_without_duplicate(self):
        submission = self.submit()
        original = self.world.act
        def interrupt(actor, key, data):
            if key.endswith(":reply"):
                raise RuntimeError("interrupted after receiving receipt")
            return original(actor, key, data)
        with patch.object(self.world, "act", side_effect=interrupt):
            with self.assertRaises(RuntimeError): self.receive(submission)
        first = self.receive(submission)
        self.assertEqual(self.receive(submission), first)
        with self.world.s.transaction() as db:
            receipts = [a for a in self.world.s.rows(db, "artifact", "steward") if a["title"] == "Report receiving result"]
        self.assertEqual(len(receipts), 1)

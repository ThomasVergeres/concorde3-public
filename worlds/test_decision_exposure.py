import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from worlds.decision_exposure import audit, match
from worlds.forensics import capture_file, write_json
from worlds.store import digest


class DecisionExposureTests(unittest.TestCase):
    def setUp(self):
        self.value = {"world": {"remaining_seconds": 600}, "message": "Theo’s inquiry"}
        self.decision = {"seq": 10, "actor": "seller", "at": 100}
        self.exposure = {"seq": 9, "actor": "seller", "at": 99,
                         "body": {"section": "help", "result_hash": digest(self.value)}}

    def row(self, value, kind="command_execution"):
        return json.dumps({"type": "item.completed", "item": {
            "type": kind, "aggregated_output": json.dumps(value)}})

    def test_returned_unicode_facts_match_before_receipt(self):
        log = self.row(self.value) + "\n" + self.row({"event": 10, "result": {}})
        result = match(log, [self.exposure], self.decision)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matches"][0]["world"]["remaining_seconds"], 600)

    def test_later_facts_or_assistant_claims_are_not_exposure(self):
        effect = self.row({"event": 10, "result": {}})
        for log in (effect + "\n" + self.row(self.value),
                    self.row(self.value, "agent_message") + "\n" + effect, self.row(self.value)):
            self.assertEqual(match(log, [self.exposure], self.decision)["status"], "unmatched")

    def test_actor_time_sequence_and_exact_digest_all_matter(self):
        log = self.row(self.value) + "\n" + self.row({"event": 10, "result": {}})
        for change in ({"actor": "buyer"}, {"seq": 11}, {"at": 101},
                       {"body": {"section": "help", "result_hash": "wrong"}}):
            self.assertEqual(match(log, [{**self.exposure, **change}], self.decision)["status"], "unmatched")

    def test_duplicate_action_and_truncated_log_are_visible(self):
        effect = self.row({"event": 10, "result": {}})
        self.assertEqual(match(effect + "\n" + effect, [], self.decision)["status"], "unmatched")
        result = match(self.row(self.value) + "\n" + effect + '\n{"partial"', [self.exposure], self.decision)
        self.assertEqual(result["incomplete_lines"], [3])
        self.assertEqual(result["status"], "matched")

    def test_captured_audit_rejects_corruption_and_other_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); database = root / "source.sqlite"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE events(seq INTEGER,at REAL,actor TEXT,kind TEXT,body TEXT)")
                db.execute("INSERT INTO events VALUES (9,99,'seller','exposure',?)", (json.dumps(self.exposure["body"]),))
                db.execute("INSERT INTO events VALUES (10,100,'seller','offer','{}')")
                db.execute("INSERT INTO events VALUES (11,101,'buyer','message','{}')")
            log = root / "source.jsonl"
            log.write_text(self.row(self.value) + "\n" + self.row({"event": 10, "result": {}}))
            file = capture_file(root, log, "subjects/seller/.concorde2/harness-logs/act.1.work.jsonl")
            manifest = {"worlds": {"case": {"database": capture_file(root, database, "world.sqlite"), "files": [file]}}}
            write_json(root / "rounds/00.json", manifest)
            self.assertEqual(audit(root, 0, "case", "seller", "act.1", 10)["status"], "matched")
            with self.assertRaises(ValueError): audit(root, 0, "case", "seller", "act.1", 11)
            (root / file["blob"]).write_bytes(b"changed")
            with self.assertRaises(ValueError): audit(root, 0, "case", "seller", "act.1", 10)


if __name__ == "__main__": unittest.main()

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from experiments.incident_attribution import receipt_lines, contains_time, locate_observation
from experiments import incident_attribution as attribution
from worlds.forensics import blob, write_json


class AttributionTests(unittest.TestCase):
    def test_receiving_bundle_keeps_private_task_and_declared_request_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audit=root/"audit"; database=root/"world.sqlite"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE records(id TEXT,kind TEXT,owner TEXT,body TEXT)")
                for identity,kind,owner,body in [
                    ("request","message","buyer",{"text":"Send a synthetic example", "at":1}),
                    ("product","artifact","subject",{"at":2,"content":{"records":["synthetic"]},"source_refs":["request"]}),
                    ("result","observation","buyer",{"at":3,"artifact":"product","task":{"records":["private-real-data"]},"outcome":{"status":"failed"}})]:
                    db.execute("INSERT INTO records VALUES (?,?,?,?)",(identity,kind,owner,json.dumps(body)))
            write_json(audit/"rounds/01.json",{"worlds":{"w":{"database":blob(audit,database.read_bytes())}}})
            sources=attribution.receiving_sources(audit,1,"w","result")
            self.assertIn("synthetic",sources["targeted/w/artifact/product"]["text"])
            self.assertIn("private-real-data",sources["targeted/w/receiving/result"]["text"])
            self.assertIn("Send a synthetic example",sources["targeted/w/declared-source/request"]["text"])
            self.assertIn("not proof",sources["targeted/w/receiving-boundary/result"]["text"])

    def test_receiving_observation_does_not_substitute_receiver_for_author(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); audit = root/"audit"; database = root/"world.sqlite"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE records(id TEXT,kind TEXT,owner TEXT,body TEXT)")
                for identity, producer in (("mine", "subject"), ("theirs", "incumbent")):
                    db.execute("INSERT INTO records VALUES (?,?,?,?)", (identity,"artifact","buyer",json.dumps({"producer":producer})))
                    db.execute("INSERT INTO records VALUES (?,?,?,?)", ("o-"+identity,"observation","buyer",json.dumps({"artifact":identity})))
            write_json(audit/"rounds/01.json", {"worlds":{"w":{"database":blob(audit,database.read_bytes())}}})
            with patch("experiments.incident_attribution.locate", return_value={"status":"receipt_observed","activation_ids":["act.old"]}) as locate:
                mine = locate_observation(audit,1,"w","subject","o-mine")
                self.assertEqual(mine["creation"]["activation_ids"],["act.old"])
                self.assertEqual(mine["receiver"],"buyer")
                locate.assert_called_once_with(audit,1,"w","subject","mine")
                theirs = locate_observation(audit,1,"w","subject","o-theirs")
                self.assertEqual(theirs["status"],"non_subject_product")
                self.assertEqual(locate.call_count,1)

    def test_receipt_requires_actual_successful_return_not_mentions(self):
        receipt = {"event": 7, "result": {"id": "artifact", "kind": "artifact"}}
        item = {"type": "command_execution", "exit_code": 0, "aggregated_output": json.dumps(receipt)}
        good = {"type": "item.completed", "item": item}
        self.assertEqual(receipt_lines(json.dumps(good), receipt), [1])
        for changed in ({**good, "type": "item.started"},
                        {**good, "item": {**item, "exit_code": 1}},
                        {**good, "item": {**item, "aggregated_output": json.dumps({**receipt, "event": 8})}},
                        {**good, "item": {"type": "agent_message", "text": json.dumps(receipt)}}):
            self.assertEqual(receipt_lines(json.dumps(changed), receipt), [])

    def test_creation_must_precede_finish_and_follow_start(self):
        activation = {"started": "1970-01-01T00:00:10Z", "finished": "1970-01-01T00:00:20Z"}
        self.assertTrue(contains_time(activation, 15))
        self.assertFalse(contains_time(activation, 9))
        self.assertFalse(contains_time(activation, 21))
        self.assertFalse(contains_time({"started": activation["started"]}, 15))

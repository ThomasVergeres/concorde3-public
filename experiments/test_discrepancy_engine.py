import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from experiments.discrepancy_campaign import (prepare, arm, maybe_curate,
    review_round, ARMS, DURATION)
from experiments.discrepancy_engine import (build_packet, fit_sources,
    targeted_case_packet, validate_review, validate_curation, review_response_schema, _world_rows,
    probe_catalog, curation_response_schema, intention_context, receiving_episode_key, correspondence_context)
from experiments.discrepancy_repair import (baseline_red, baseline_disposition,
    candidate_case_status, candidate_green, lab_command, labels, load_contract)
from experiments.discrepancy_store import DiscrepancyStore
from experiments.discrepancy_transport import (MetaDriver, OUTER_SCHEMA,
    process_start_ticks, recorded_error, same_process, terminate_group)
from worlds.forensics import blob, replay, write_json


def envelope(seq, changes):
    raw = json.dumps({"seq": seq, "changes": changes}, separators=(",", ":"))
    return ('{"event":' + raw + ',"hash":"' + hashlib.sha256(raw.encode()).hexdigest() + '"}\n').encode()


class StoreTests(unittest.TestCase):
    def test_reprocessing_does_not_silently_reclassify_or_orphan_an_existing_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"cases.sqlite", clock=lambda: 10)
            args = dict(round_number=5, world="w", subject="a", category="failure", polarity="suspected",
                severity="medium", evidence_ref="w/outcome/one", observed="failed", exposure={})
            original = store.nominate(**args, episode_key="old grouping")
            self.assertEqual(store.nominate(**args, episode_key="new grouping"), original)
            self.assertEqual(len(store.rows("cases")), 1)
            self.assertEqual(len(store.rows("signals")), 1)

    def test_out_of_order_sighting_keeps_true_first_and_last_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"cases.sqlite", clock=lambda: 10)
            args = dict(world="w", subject="a", category="failure", polarity="suspected",
                severity="medium", observed="failed", exposure={}, episode_key="episode")
            identity = store.nominate(round_number=5, evidence_ref="o5", **args)
            store.nominate(round_number=2, evidence_ref="o2", **args)
            case = store.rows("cases", "id=?", (identity,))[0]
            self.assertEqual((case["first_round"], case["last_round"], case["sightings"]), (2, 5, 2))

    def test_fixed_rounds_and_terminal_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp) / "cases.sqlite", clock=lambda: 10)
            store.schedule_rounds(0, 10800)
            self.assertEqual(len(store.rows("rounds")), 19)
            self.assertEqual([r["number"] for r in store.rows("rounds") if r["number"]], list(range(1, 19)))
            store.start_round(0); store.finish_round(0, "captured")
            with self.assertRaises(ValueError): store.finish_round(0, "captured")
            with self.assertRaises(ValueError): store.schedule_rounds(0, 10800)

    def test_signal_dedupes_causal_episode_but_counts_sightings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp) / "cases.sqlite", clock=lambda: 10)
            args = dict(world="w", subject="a", category="failure", polarity="suspected",
                severity="medium", evidence_ref="w/a/act", observed="failed",
                exposure={"opportunities": 1}, episode_key="w:a:act")
            one = store.nominate(round_number=1, **args)
            self.assertEqual(one, store.nominate(round_number=1, **args))
            self.assertEqual(store.rows("cases")[0]["sightings"], 1)
            args["evidence_ref"] = "w/a/act/later"
            self.assertEqual(one, store.nominate(round_number=2, **args))
            self.assertEqual(store.rows("cases")[0]["sightings"], 2)
            self.assertEqual(len(store.rows("signals")), 2)


class PacketTests(unittest.TestCase):
    def test_receiving_episode_preserves_material_boundaries_and_unknowns(self):
        record = {"artifact": "a", "project": "p", "work_id": "w", "task": {"t": 1},
            "preferences": "quiet", "outcome": {"status": "passed"}}
        first = receiving_episode_key("world", "o1", "buyer", record)
        self.assertEqual(first, receiving_episode_key("world", "o2", "buyer", {**record, "at": 20}))
        for field, value in (("artifact", "b"), ("project", "q"), ("work_id", "v"),
                ("preferences", "busy"), ("task", {"t": 2}), ("outcome", {"status": "failed"})):
            self.assertNotEqual(first, receiving_episode_key("world", "o1", "buyer", {**record, field: value}))
        self.assertNotEqual(first, receiving_episode_key("world", "o1", "other", record))
        self.assertNotEqual(first, receiving_episode_key("other-world", "o1", "buyer", record))
        del record["work_id"]
        self.assertNotEqual(receiving_episode_key("world", "o1", "buyer", record), receiving_episode_key("world", "o2", "buyer", record))

    def test_intention_context_rotates_without_conflating_rest_and_completion(self):
        state = {"items": {str(i): {"id": str(i), "kind": "intention", "status": "active", "text": "界"*2000,
            "attention": {"effort_state": "dormant"}} for i in range(11)}}
        state["items"]["closed"] = {"id": "closed", "kind": "intention", "status": "attained"}
        seen = set()
        for number in range(3):
            result = intention_context(state, number)
            self.assertEqual(result["active_intentions"], 11)
            self.assertEqual(result["omitted_intentions"], 7)
            self.assertLess(len(json.dumps(result).encode()), 14000)
            for item in result["items"]:
                seen.add(item["id"])
                self.assertTrue(item["text_truncated"])
                self.assertEqual(item["attention"]["effort_state"], "dormant")
        self.assertEqual(seen, set(state["items"])-{"closed"})

    def test_world_database_digest_must_match_before_packet_uses_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); audit = root/"audit"
            old, current = self.databases(root)
            ref = blob(audit, current)
            manifest = {"worlds": {"w": {"database": ref}}}
            self.assertTrue(_world_rows(audit, manifest, "w", None)[1])
            with sqlite3.connect(audit/ref["blob"]) as db:
                db.execute("UPDATE records SET owner='different-author'")
            with self.assertRaisesRegex(ValueError, "invalid forensic blob"):
                _world_rows(audit, manifest, "w", None)

    def test_customer_exposure_is_not_subject_exposure_and_producer_is_explicit(self):
        for exposed_to, expected in (("buyer", False), ("subject", True)):
            with self.subTest(exposed_to=exposed_to), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); audit = root / "audit"
                state = {"seq": 1, "version": 2, "mode": "running", "config": {},
                    "items": {"long-purpose": {"id": "long-purpose", "node": "undertaking", "kind": "intention", "status": "active",
                        "text": "Sustain commitments as availability changes", "attention": {"effort_state": "dormant"}}},
                    "activations": {}, "programs": {}}
                history = envelope(1, [{"field": k, "value": v} for k,v in state.items() if k != "seq"])
                self.databases(root)
                with sqlite3.connect(root / "new.sqlite") as db:
                    db.execute("UPDATE events SET actor=? WHERE kind='exposure'", (exposed_to,))
                    db.execute("INSERT INTO events VALUES (3,6,'buyer','defer','{\"after_seconds\":1800}')")
                    db.execute("INSERT INTO events VALUES (4,7,'subject','message','{}')")
                prefix = "subjects/subject/.concorde2/"
                manifest = {"number": 0, "started": 0, "finished": 10, "errors": [], "worlds": {"w": {
                    "event_watermark": 4, "database": blob(audit, (root / "new.sqlite").read_bytes()), "sessions": [],
                    "files": [{"path": prefix+"state.json", **blob(audit, json.dumps(state).encode())},
                              {"path": prefix+"events.jsonl", **blob(audit, history)}]}}}
                write_json(audit/"rounds/00.json", manifest)
                store = DiscrepancyStore(root/"cases.sqlite")
                packet = build_packet(audit, 0, {"started": 0, "cutoff": 100, "worlds": {"w": "unused"}}, store)
                signal = store.rows("signals", "category='receiving_failure'")[0]
                self.assertEqual(json.loads(signal["exposure"])["outcome_served_to_subject"], expected)
                evidence = json.loads(packet["sources"]["w/outcome/outcome-new"]["text"])
                self.assertEqual(evidence["provenance"]["artifact_producer"], "subject")
                self.assertEqual(evidence["provenance"]["outcome_served_to_subject"], expected)
                self.assertEqual(evidence["provenance"]["producing_activation"], "not established by this source")
                trajectory = json.loads(packet["sources"]["w/trajectory"]["text"])
                intentions = json.loads(packet["sources"]["w/durable-intentions"]["text"])
                self.assertEqual(intentions["items"][0]["text"], "Sustain commitments as availability changes")
                self.assertIn("not proof", intentions["interpretation"])
                self.assertNotIn("world_events", trajectory)
                self.assertTrue(all(e["actor"]=="subject" for e in trajectory["subject_world_events"]))
                outsiders = json.loads(packet["sources"]["w/non-subject-world-events"]["text"])
                self.assertTrue(any(e["kind"]=="defer" and e["actor"]=="buyer" for e in outsiders["events"]))

    def test_unassessed_incumbent_is_evidence_not_a_subject_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); audit = root / "audit"
            state = {"seq": 1, "version": 2, "mode": "running", "config": {},
                "items": {}, "activations": {}, "programs": {}}
            history = envelope(1, [{"field": key, "value": value} for key, value in state.items() if key != "seq"])
            database = root / "world.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript("CREATE TABLE events(seq INTEGER,at REAL,actor TEXT,kind TEXT,body TEXT); CREATE TABLE records(id TEXT,kind TEXT,owner TEXT,body TEXT); CREATE TABLE calls(id TEXT,actor TEXT,category TEXT,at REAL,deadline REAL,status TEXT,body TEXT);")
                db.execute("INSERT INTO records VALUES ('incumbent-output','artifact','incumbent',?)",
                    (json.dumps({"producer": "incumbent", "at": 2, "title": "Pilot", "content": {"x": 1}}),))
                db.execute("INSERT INTO records VALUES ('later-subject-output','artifact','self',?)",
                    (json.dumps({"at": 9, "title": "Pilot", "content": {"x": 2}}),))
                db.execute("INSERT INTO records VALUES ('unassessed','observation','buyer',?)",
                    (json.dumps({"at": 5, "artifact": "incumbent-output", "outcome": {"status": "unassessed"}}),))
            prefix = "subjects/self/.concorde2/"
            manifest = {"number": 0, "started": 0, "finished": 1, "errors": [], "worlds": {"w": {
                "event_watermark": 0, "database": blob(audit, database.read_bytes()), "sessions": [],
                "files": [{"path": prefix+"state.json", **blob(audit, json.dumps(state).encode())},
                          {"path": prefix+"events.jsonl", **blob(audit, history)}]}}}
            write_json(audit/"rounds/00.json", manifest)
            store = DiscrepancyStore(root/"cases.sqlite")
            packet = build_packet(audit, 0, {"started": 0, "cutoff": 100, "worlds": {"w": "unused"}}, store)
            self.assertIn("w/outcome/unassessed", packet["sources"])
            self.assertFalse(store.rows("signals", "category='semantic_outcome_unassessed'"))
            evidence = json.loads(packet["sources"]["w/outcome/unassessed"]["text"])
            evaluated = evidence["provenance"]["evaluated_artifact"]
            self.assertEqual((evaluated["id"], evaluated["created_at"]), ("incumbent-output", 2))
            products = json.loads(packet["sources"]["w/subject-product-identities"]["text"])
            later = products["products"][0]
            self.assertEqual((later["id"], later["created_at"]), ("later-subject-output", 9))
            self.assertNotEqual(later["content_sha256"], evaluated["content_sha256"])

    def test_global_packet_budget_preserves_keys_and_utf8_trajectories(self):
        sources = {}
        for index in range(12):
            sources[f"world-{index}/trajectory"] = {"text": "観察" * 20000,
                "truncated": False}
            for deep in range(12):
                sources[f"world-{index}/deep-{deep}"] = {"text": "証拠" * 12000,
                    "truncated": False}
        keys = set(sources)
        receipt = fit_sources(sources)
        self.assertEqual(set(sources), keys)
        self.assertLessEqual(len(json.dumps(sources, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()), 260000)
        self.assertEqual(receipt["maximum_bytes"], 260000)
        self.assertTrue(all(sources[f"world-{index}/trajectory"]["text"]
                            for index in range(12)))
        self.assertGreater(receipt["truncated_sources"], 0)

    def test_repeated_unassessed_work_is_one_episode_but_changed_contract_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); audit = root/"audit"
            state = {"seq": 1, "version": 2, "mode": "running", "config": {}, "items": {}, "activations": {}, "programs": {}}
            history = envelope(1, [{"field": k, "value": v} for k,v in state.items() if k != "seq"])
            self.databases(root)
            with sqlite3.connect(root/"new.sqlite") as db:
                db.execute("DELETE FROM records WHERE kind='observation'")
                for identity, task in (("u1", {"need": "one"}), ("u2", {"need": "one"}), ("u3", {"need": "different"})):
                    db.execute("INSERT INTO records VALUES (?,'observation','buyer',?)", (identity, json.dumps({
                        "at": 5, "artifact": "artifact-old", "project": "project:buyer", "work_id": "work-1",
                        "task": task, "outcome": {"status": "unassessed", "reason": "semantic judgment needed"}})))
            prefix = "subjects/subject/.concorde2/"
            manifest = {"number": 0, "started": 0, "finished": 10, "errors": [], "worlds": {"w": {
                "event_watermark": 2, "database": blob(audit, (root/"new.sqlite").read_bytes()), "sessions": [],
                "files": [{"path": prefix+"state.json", **blob(audit, json.dumps(state).encode())},
                          {"path": prefix+"events.jsonl", **blob(audit, history)}]}}}
            write_json(audit/"rounds/00.json", manifest)
            store = DiscrepancyStore(root/"cases.sqlite")
            packet = build_packet(audit, 0, {"started": 0, "cutoff": 100, "worlds": {"w": "unused"}}, store)
            signals = store.rows("signals", "category='semantic_outcome_unassessed'")
            self.assertEqual(len(signals), 3)
            self.assertEqual(len({s["case_id"] for s in signals}), 2)
            self.assertTrue(all("w/outcome/"+name in packet["sources"] for name in ("u1", "u2", "u3")))
            cases = store.rows("cases", "category='semantic_outcome_unassessed'")
            self.assertEqual(sorted(c["sightings"] for c in cases), [1, 2])

    def test_correspondence_does_not_disappear_when_no_new_event_occurs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audit=root/"audit"; self.databases(root)
            request_text="Please send the optional comparison we discussed. "+"Specific context. "*80
            with sqlite3.connect(root/"new.sqlite") as db:
                db.execute("INSERT INTO records VALUES ('request','message','buyer',?)",
                    (json.dumps({"at":2,"to":"subject","text":request_text}),))
                db.execute("INSERT INTO events VALUES (3,3,'buyer','exposure',?)",
                    (json.dumps({"record_ids":["request"]}),))
                db.execute("INSERT INTO events VALUES (4,4,'operator','experiment_stimulus',?)",
                    (json.dumps({"message_id":"request","origin":"operator-authored simulated inquiry"}),))
            state={"seq":1,"version":2,"mode":"running","config":{},"items":{},"activations":{},"programs":{}}
            history=envelope(1,[{"field":k,"value":v} for k,v in state.items() if k!="seq"])
            prefix="subjects/subject/.concorde2/"
            files=[{"path":prefix+"state.json",**blob(audit,json.dumps(state).encode())},
                   {"path":prefix+"events.jsonl",**blob(audit,history)}]
            world={"event_watermark":4,"database":blob(audit,(root/"new.sqlite").read_bytes()),"sessions":[],"files":files}
            for n,finished in ((0,10),(1,20)):
                write_json(audit/"rounds"/f"{n:02d}.json",{"number":n,"started":finished-1,"finished":finished,"errors":[],"worlds":{"w":world}})
            packet=build_packet(audit,1,{"started":0,"cutoff":100,"worlds":{"w":"unused"}},DiscrepancyStore(root/"cases.sqlite"))
            self.assertEqual(packet["denominators"]["w/subject"]["inbound_messages"],0)
            context=json.loads(packet["sources"]["w/correspondence-continuity"]["text"])
            self.assertEqual(context["inbound_count"],1)
            self.assertEqual(context["inbound"][0]["id"],"request")
            self.assertEqual(context["inbound"][0]["delivered_at"],2)
            self.assertIsNone(context["inbound"][0]["first_observed_serving"])
            self.assertEqual(context["inbound"][0]["origin_annotation"]["origin"],"operator-authored simulated inquiry")
            self.assertEqual(context["inbound"][0]["origin_annotation"]["event"],4)
            self.assertIn("not an outstanding-duty",context["interpretation"])
            self.assertTrue(context["inbound"][0]["text_truncated"])
            target=targeted_case_packet(audit,1,packet,"w/subject",evidence=["w/correspondence-continuity"])
            deep=json.loads(target["sources"]["targeted/w/subject/correspondence"]["text"])
            self.assertEqual(deep["inbound"][0]["text"],request_text)
            self.assertFalse(deep["inbound"][0]["text_truncated"])

    def databases(self, root):
        def make(path, current):
            with sqlite3.connect(path) as db:
                db.executescript("CREATE TABLE events(seq INTEGER,at REAL,actor TEXT,kind TEXT,body TEXT); CREATE TABLE records(id TEXT,kind TEXT,owner TEXT,body TEXT); CREATE TABLE calls(id TEXT,actor TEXT,category TEXT,at REAL,deadline REAL,status TEXT,body TEXT);")
                db.execute("INSERT INTO events VALUES (1,1,'_operator','seed','{}')")
                db.execute("INSERT INTO records VALUES ('artifact-old','artifact','subject',?)",
                           (json.dumps({"producer": "subject", "content": {"x": 1}}),))
                if current:
                    observation = {"at": 5, "artifact": "artifact-old", "work_id": "work-1",
                        "outcome": {"status": "failed", "reason": "receiver rejected structure"}}
                    db.execute("INSERT INTO records VALUES ('outcome-new','observation','buyer',?)", (json.dumps(observation),))
                    db.execute("INSERT INTO events VALUES (2,5,'subject','exposure',?)",
                               (json.dumps({"record_ids": ["outcome-new"]}),))
            return path.read_bytes()
        return make(root / "old.sqlite", False), make(root / "new.sqlite", True)

    def test_correspondence_serving_receipts_include_inspect_and_browse_not_other_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audit=root/"audit"; self.databases(root)
            records=[(str(i),"message","buyer",{"to":"subject","at":i,"text":"é"*600}) for i in range(12)]
            with sqlite3.connect(root/"new.sqlite") as db:
                for seq,who,kind,body in ((3,"subject","inspect",{"result":{"id":"0"}}),
                                          (4,"subject","browse",{"result":{"records":[{"id":"1"}]}}),
                                          (5,"buyer","exposure",{"record_ids":["2"]})):
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?)",(seq,seq,who,kind,json.dumps(body)))
            manifest={"worlds":{"w":{"database":blob(audit,(root/"new.sqlite").read_bytes())}}}
            result=correspondence_context(audit,manifest,"w","subject",records)
            self.assertEqual(result["inbound_without_serving_receipt"],10)
            self.assertEqual(result["omitted_inbound"],4)
            self.assertEqual([r["id"] for r in result["inbound"]],["2","3","4","5","8","9","10","11"])
            self.assertTrue(all(r["text_truncated"] and len(r["text"].encode())<=500 for r in result["inbound"]))
            result=correspondence_context(audit,manifest,"w","subject",records[:3])
            self.assertEqual([r["first_observed_serving"] for r in result["inbound"]],[3,4,None])

    def test_event_watermark_and_record_identity_avoid_capture_window_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); audit = root / "audit"; (audit / "rounds").mkdir(parents=True)
            changes = [
                {"field": "version", "value": 2},
                {"field": "mode", "value": "running"},
                {"field": "config", "value": {"model": "gpt-5.6-luna", "effort": "xhigh", "global": []}},
                {"field": "items", "key": "purpose", "value": {"id": "purpose", "attention": {"weight": 1, "effort_state": "waiting"}}},
                {"field": "activations", "key": "act.1", "value": {"id": "act.1", "status": "failed", "phase": "work", "started": "2026-01-01T00:00:00Z", "finished": "2026-01-01T00:01:00Z"}},
            ]
            history = envelope(1, changes); state = replay(history, 1)
            # The incident predates more than three later activations. Retrieval
            # must not confuse recency with the author of the failed artifact.
            for index in range(2, 6):
                changes.append({"field": "activations", "key": f"act.{index}", "value": {"id": f"act.{index}", "status": "completed", "started": f"2026-01-01T00:0{index}:00Z"}})
            history = envelope(1, changes); state = replay(history, 1)
            old_state = {**state, "activations": {}}
            old_db, new_db = self.databases(root)
            prefix = "subjects/subject/.concorde2/"
            old_files = [{"path": prefix + "state.json", **blob(audit, json.dumps(old_state).encode())}]
            current_files = [{"path": prefix + "state.json", **blob(audit, json.dumps(state).encode())},
                {"path": prefix + "events.jsonl", **blob(audit, history)},
                {"path": prefix + "contexts/act.1.work.json", **blob(audit, json.dumps({"sequence": 1, "served": "actual", "body": "x"*8000, "last": "late-important-context"}).encode())},
                {"path": prefix + "harness-logs/act.1.work.jsonl", **blob(audit, b'{"tool":"state"}\n')}]
            old_manifest = {"number": 0, "started": 0, "finished": 100, "errors": [], "worlds": {"w": {
                "event_watermark": 1, "database": blob(audit, old_db), "files": old_files, "sessions": []}}}
            new_manifest = {"number": 1, "scheduled": 600, "started": 101, "finished": 102, "errors": [], "worlds": {"w": {
                "event_watermark": 2, "database": blob(audit, new_db), "files": current_files, "sessions": []}}}
            write_json(audit / "rounds/00.json", old_manifest); write_json(audit / "rounds/01.json", new_manifest)
            store = DiscrepancyStore(root / "cases.sqlite")
            packet = build_packet(audit, 1, {"started": 0, "cutoff": 10800, "worlds": {"w": "unused"}}, store)
            signals = store.rows("signals")
            self.assertTrue(any(s["category"] == "receiving_failure" for s in signals))
            self.assertTrue(any(s["evidence_ref"] == "w/outcome/outcome-new" for s in signals))
            context = packet["sources"]["w/subject/activation/act.1/work/served-context"]
            self.assertIn("actual", context["text"])
            boundary = packet["sources"]["w/subject/activation/act.1/boundary"]["text"]
            self.assertIn("replay_qualification", boundary)
            targeted = targeted_case_packet(audit, 1, packet, "w/subject", activation_ids=["act.1"])
            self.assertIn("targeted/w/subject/act.1/work/served-context", targeted["sources"])
            self.assertIn("pre_state_sha256", targeted["sources"]["targeted/w/subject/act.1/boundary"]["text"])
            served = targeted["sources"]["targeted/w/subject/act.1/work/served-context"]
            self.assertFalse(served["truncated"])
            self.assertIn("late-important-context", served["text"])
            self.assertIn("curation/probe-catalog", targeted["sources"])
            with self.assertRaisesRegex(ValueError, "target activation absent"):
                targeted_case_packet(audit, 1, packet, "w/subject", activation_ids=["act.missing"])
            # A receiving outcome can point to an older producing activation.
            # Canonical receipt attribution must outrank a recent-neighbor guess.
            with patch("experiments.incident_attribution.locate_observation", return_value={
                    "observation": "outcome-new", "artifact_producer": "subject",
                    "creation": {"status": "receipt_observed", "activation_ids": ["act.1"]}}):
                joined = targeted_case_packet(audit, 1, packet, "w/subject", evidence=["w/outcome/outcome-new"])
            inventory = json.loads(joined["sources"]["targeted/w/subject/inventory"]["text"])
            self.assertEqual(inventory["selected_neighboring_activations"], ["act.1"])
            self.assertIn("canonical", inventory["selection_basis"])
            self.assertIn("targeted/w/subject/outcome-new/attribution", joined["sources"])
            with patch("experiments.incident_attribution.locate_observation", return_value={
                    "observation": "outcome-new", "artifact_producer": "buyer", "status": "non_subject_product"}):
                unknown = targeted_case_packet(audit, 1, packet, "w/subject", evidence=["w/outcome/outcome-new"])
            inventory = json.loads(unknown["sources"]["targeted/w/subject/inventory"]["text"])
            self.assertEqual(inventory["selected_neighboring_activations"], [])
            self.assertIn("must not be guessed", inventory["selection_basis"])


class ReviewContractTests(unittest.TestCase):
    def test_curator_format_cannot_propose_a_probe_while_declining_qualification(self):
        schema=curation_response_schema(self.packet(),"case-a")
        qualified,other=schema["properties"]["note"]["anyOf"]
        self.assertEqual(other["properties"]["probe_family"]["enum"],["none"])
        self.assertNotIn("baseline_cells",other["properties"])
        self.assertEqual(qualified["properties"]["decision"]["enum"],["qualified_existing_probe"])
        self.assertIn("baseline_cells",qualified["required"])

    def test_catalog_keeps_both_contracts_without_duplicate_seed_graphs(self):
        catalog=probe_catalog()
        self.assertLess(len(json.dumps(catalog).encode()),100000)
        for entry in catalog.values():
            self.assertEqual(set(entry["conditions"]),{"challenge","control"})
            for condition in entry["conditions"].values():
                self.assertNotIn("semantic_signature",condition["facts"])
                self.assertIn("requirements",condition["facts"])
                self.assertIn("desk.json",condition["exchange"])
                self.assertTrue(condition["facts_sha256"])

    def test_schema_restricts_identifiers_not_substantive_dispositions(self):
        packet = {**self.packet(), "denominators": {"w/a": {"activation_ids": ["act-1"]}}}
        schema = review_response_schema(packet)
        note = schema["properties"]["note"]
        self.assertEqual(note["type"], "object")
        fields = note["properties"]["judgments"]["items"]["properties"]
        self.assertEqual(fields["subject"]["enum"], ["w/a"])
        self.assertEqual(fields["evidence"]["items"]["enum"], ["w/trajectory"])
        self.assertIn("", fields["case_id"]["enum"])
        self.assertIn("healthy_control", fields["disposition"]["enum"])
        self.assertIn("uncertain", fields["likely_layer"]["enum"])
        self.assertEqual(fields["activation_ids"]["items"]["enum"], ["act-1"])
        empty = review_response_schema({**self.packet(), "denominators": {}})
        self.assertEqual(empty["properties"]["note"]["properties"]["judgments"]["maxItems"], 0)

    def test_structured_review_and_legacy_string_have_identical_strict_validation(self):
        packet = {**self.packet(), "denominators": {"w/a": {}}}
        value = {"synopsis": "s", "resource_comment": "r", "unknowns": [],
            "judgments": [], "positives": [{"subject": "w/a", "observed": "rested",
                "evidence": ["w/trajectory"]}]}
        structured = {"actions": [], "finish": True, "note": value}
        legacy = {**structured, "note": json.dumps(value)}
        self.assertEqual(validate_review(structured, packet), validate_review(legacy, packet))
        value["positives"][0]["subject"] = "a"
        self.assertTrue(validate_review(structured, packet)["rejected_items"])

    def test_existing_case_needs_no_new_episode_key(self):
        row = {"case_id": "case-prior", "subject": "w/a", "category": "x", "severity": "low",
            "confidence": "high", "likely_layer": "model", "observed": "still present",
            "available_context": "exact evidence", "hindsight": "none", "preferable": "grounded change",
            "ideas": [], "evidence": ["w/trajectory"], "disposition": "watch"}
        def response(): return {"actions": [], "finish": True, "note": json.dumps({
            "synopsis": "s", "judgments": [row], "positives": [], "unknowns": [], "resource_comment": "r"})}
        result = validate_review(response(), self.packet())
        self.assertEqual(result["judgments"], [row])
        self.assertFalse(result["rejected_items"])
        row["case_id"] = ""
        self.assertTrue(validate_review(response(), self.packet())["rejected_items"])

    def packet(self):
        return {"sources": {"w/trajectory": {"text": "x"}},
                "nominated_case_ids": ["case-a"], "known_case_ids": ["case-a", "case-prior"]}

    def test_prior_supplied_case_can_be_reviewed_but_unknown_case_cannot(self):
        judgment = {"case_id": "case-prior", "episode_key": "", "subject": "w/a",
            "category": "trajectory", "severity": "low", "confidence": "medium",
            "likely_layer": "uncertain", "observed": "pattern continued",
            "available_context": "prior case was supplied", "hindsight": "none",
            "preferable": "proportionate response", "ideas": ["observe"],
            "evidence": ["w/trajectory"], "disposition": "watch"}
        response = {"actions": [], "finish": True, "note": json.dumps({"synopsis": "s",
            "judgments": [judgment], "positives": [], "unknowns": [],
            "resource_comment": "bounded"})}
        self.assertEqual(validate_review(response, self.packet())["judgments"][0]["case_id"],
                         "case-prior")
        judgment["case_id"] = "case-unknown"
        response["note"] = json.dumps({"synopsis": "s", "judgments": [judgment],
            "positives": [], "unknowns": [], "resource_comment": "bounded"})
        result = validate_review(response, self.packet())
        self.assertEqual(result["judgments"], [])
        self.assertIn("unknown case", result["rejected_items"][0]["reason"])

    def test_sol_can_nominate_new_soft_episode(self):
        judgment = {"case_id": "", "episode_key": "neglected-ready-request-7", "subject": "w/a",
            "category": "attention", "severity": "medium", "confidence": "medium",
            "likely_layer": "uncertain", "observed": "ready request received no response",
            "available_context": "request was served", "hindsight": "later miss",
            "preferable": "address or consciously defer", "ideas": ["compare healthy quiet"],
            "evidence": ["w/trajectory"], "disposition": "curate"}
        response = {"actions": [], "finish": True, "note": json.dumps({"synopsis": "s",
            "judgments": [judgment], "positives": [], "unknowns": [], "resource_comment": "bounded"})}
        self.assertEqual(validate_review(response, self.packet())["judgments"][0]["episode_key"], judgment["episode_key"])
        judgment["episode_key"] = ""
        response["note"] = json.dumps({"synopsis": "s", "judgments": [judgment],
            "positives": [], "unknowns": [], "resource_comment": "bounded"})
        result = validate_review(response, self.packet())
        self.assertEqual(result["judgments"], [])
        self.assertIn("episode key", result["rejected_items"][0]["reason"])

    def test_curation_never_calls_analogous_fixture_exact_replay(self):
        value = {"case_id": "case-a", "decision": "qualified_existing_probe",
            "replay_status": "inspectable", "replay_omissions": ["not atomic"],
            "prevention_or_escape": "prevention", "actual_context": ["w/trajectory"],
            "hindsight": [], "preferable": "respond proportionately", "alternatives": ["manual", "rest"],
            "probe_family": "AR01", "why_probe_matches": "same later attention mechanism",
            "baseline_cells": {"variants": ["challenge", "control"], "profile": "situated",
                "world_seed": 44, "draws": 1, "starts": 3, "wall": 420},
            "frozen_criteria": {"primary_outcome": "later value", "failure_condition": "no response",
                "healthy_control": "rest", "holdout": "seed 45"}, "likely_layer": "C3",
            "confidence": "medium", "evidence": ["w/trajectory"]}
        response = {"actions": [], "finish": True, "note": json.dumps(value)}
        self.assertEqual(validate_curation(response, self.packet(), "case-a")["probe_family"], "AR01")
        self.assertEqual(validate_curation({**response, "note": value}, self.packet(), "case-a"), value)
        value["actual_context"] = ["invented-context"]
        with self.assertRaisesRegex(ValueError, "actual context"):
            validate_curation({**response, "note": value}, self.packet(), "case-a")
        value["actual_context"] = ["w/trajectory"]
        value["replay_status"] = "inadequate"
        with self.assertRaisesRegex(ValueError, "inspectable"):
            validate_curation({**response, "note": value}, self.packet(), "case-a")
        value["replay_status"] = "replay-qualified"
        response["note"] = json.dumps(value)
        with self.assertRaises(ValueError): validate_curation(response, self.packet(), "case-a")


class CampaignAndRepairTests(unittest.TestCase):
    def review_root(self, root):
        manifest = {"started": 0, "cutoff": 10800, "worlds": {}}
        (root / "cohort.json").write_text(json.dumps(manifest))
        DiscrepancyStore(root / "discrepancies.sqlite").schedule_rounds(0, 10800)
        return {"round": 1, "sources": {"campaign/resources": {"text": "{}"}},
                "denominators": {}, "nominated_case_ids": [], "known_case_ids": []}

    def test_failed_review_is_terminal_but_optional_curation_cannot_rewrite_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); packet = self.review_root(root)
            common = [patch("experiments.discrepancy_campaign.forensics.snapshot", return_value={"errors": []}),
                      patch("experiments.discrepancy_campaign.forensics.digest_round"),
                      patch("experiments.discrepancy_campaign.build_packet", return_value=packet)]
            for item in common: item.start()
            try:
                with patch("experiments.discrepancy_campaign.MetaDriver", side_effect=RuntimeError("timeout")):
                    with self.assertRaisesRegex(RuntimeError, "timeout"):
                        review_round(root, 1)
            finally:
                for item in reversed(common): item.stop()
            store = DiscrepancyStore(root / "discrepancies.sqlite")
            self.assertEqual(store.rows("rounds", "number=1")[0]["status"], "failed")
            self.assertEqual(store.rows("reviews")[0]["status"], "failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); packet = self.review_root(root)
            value = {"synopsis": "quiet", "judgments": [], "positives": [],
                     "unknowns": [], "resource_comment": "bounded"}
            response = {"actions": [], "finish": True, "note": json.dumps(value)}
            common = [patch("experiments.discrepancy_campaign.forensics.snapshot", return_value={"errors": []}),
                      patch("experiments.discrepancy_campaign.forensics.digest_round"),
                      patch("experiments.discrepancy_campaign.build_packet", return_value=packet),
                      patch("experiments.discrepancy_campaign.MetaDriver", return_value=lambda *_:
                            (response, {"usage": [], "call_id": "call", "response_ref": "private"})),
                      patch("experiments.discrepancy_campaign.maybe_curate", side_effect=RuntimeError("curation failed"))]
            for item in common: item.start()
            try:
                self.assertEqual(review_round(root, 1), {**value, "rejected_items": [], "validation_status": "complete"})
            finally:
                for item in reversed(common): item.stop()
            store = DiscrepancyStore(root / "discrepancies.sqlite")
            self.assertEqual(store.rows("rounds", "number=1")[0]["status"], "reviewed")
            self.assertEqual(store.rows("reviews")[0]["status"], "completed")

    def test_meta_output_schema_is_strict_and_structured_errors_survive(self):
        def assert_strict_objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                for value in node.values():
                    assert_strict_objects(value)
            elif isinstance(node, list):
                for value in node:
                    assert_strict_objects(value)
        assert_strict_objects(OUTER_SCHEMA)
        event = json.dumps({"type": "turn.failed", "error": {"message": "schema rejected"}})
        self.assertEqual(recorded_error(event), "schema rejected")

    def test_prepare_has_twelve_selected_lives_and_arm_exact_clock(self):
        with tempfile.TemporaryDirectory() as tmp, patch("experiments.discrepancy_campaign.command", return_value="sha256:test"):
            root = Path(tmp) / "campaign"
            manifest = prepare(root, "image")
            self.assertEqual(len(manifest["worlds"]), 12)
            self.assertEqual(len(ARMS), 12)
            manifest["status"] = "qualified"; save_path = root / "cohort.json"
            save_path.write_text(json.dumps(manifest))
            with patch("experiments.discrepancy_campaign.source_hashes", return_value=manifest["source_hashes"]):
                live = arm(root)
            self.assertEqual(live["cutoff"] - live["started"], DURATION)
            self.assertEqual(len(DiscrepancyStore(root / "discrepancies.sqlite").rows("rounds")), 19)
            for name, location in live["worlds"].items():
                with sqlite3.connect(Path(location) / "world.sqlite") as db:
                    cfg = json.loads(db.execute("SELECT body FROM meta WHERE key='config'").fetchone()[0])
                self.assertEqual((cfg["model"], cfg["effort"]), ("gpt-5.6-luna", "xhigh"))

    def test_repair_lane_is_baseline_first_with_control_and_holdout(self):
        curation = {"probe_family": "AR01", "baseline_cells": {"world_seed": 7, "starts": 3, "wall": 420}}
        args = [str(v) for v in lab_command("/private/root", "/private/out", "sha256:image", curation, "baseline")]
        self.assertIn("challenge,control", args)
        self.assertIn("7,8", args)
        self.assertIn("luna-xhigh", args)
        complete_red = {("challenge", 7): "behavioral_failure", ("control", 7): "success",
                        ("challenge", 8): "success", ("control", 8): "success"}
        self.assertTrue(baseline_red(complete_red, 7))
        self.assertFalse(baseline_red({("challenge", 7): "behavioral_failure", ("control", 7): "ambiguous"}, 7))
        complete_green = {("challenge", 7): "success", ("control", 7): "success",
                          ("challenge", 8): "success", ("control", 8): "success"}
        self.assertEqual(baseline_disposition(complete_green, 7), "analogy_not_reproduced")
        split = {**complete_green, ("challenge", 8): "behavioral_failure"}
        self.assertEqual(baseline_disposition(split, 7), "probe_inconclusive")
        self.assertEqual(baseline_disposition({("challenge", 7): "runtime_failure", ("control", 7): "success"}, 7), "probe_inconclusive")
        self.assertEqual(baseline_disposition({("challenge", 7): "behavioral_failure", ("control", 7): "ambiguous"}, 7), "probe_inconclusive")
        self.assertFalse(baseline_red(complete_green, 7))
        self.assertTrue(candidate_green(complete_green, 7))
        self.assertFalse(candidate_green({**complete_green, ("challenge", 8): "behavioral_failure"}, 7))
        self.assertEqual(candidate_case_status("reject"), "candidate_rejected")
        self.assertEqual(candidate_case_status("inconclusive"), "candidate_inconclusive")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); (path / "results.json").write_text(json.dumps([
                {"variant": "challenge", "world_seed": 7, "result": {"label": "behavioral_failure"}},
                {"variant": "control", "world_seed": 7, "result": {"label": "success"}}]))
            self.assertEqual(labels(path)[("challenge", 7)], "behavioral_failure")

    def test_frozen_criteria_hash_is_checked_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            case_id = store.nominate(round_number=1, world="w", subject="a", category="x",
                polarity="suspected", severity="medium", evidence_ref="x", observed="x",
                exposure={}, episode_key="one")
            case = root / "cases" / case_id; case.mkdir(parents=True)
            curation = {"decision": "qualified_existing_probe", "probe_family": "AR01"}
            (case / "curation.json").write_text(json.dumps({"curation": curation}))
            (case / "frozen-criteria.json").write_text(json.dumps({"case_id": case_id, "probe": "AR01"}))
            store.create_job(case_id, "baseline_probe", {"criteria_sha256": "0" * 64})
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_contract(root, case_id)

    def test_dispatch_cannot_change_cells_or_verdict_after_freezing_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            case_id = store.nominate(round_number=1, world="w", subject="a", category="x",
                polarity="suspected", severity="medium", evidence_ref="x", observed="x",
                exposure={}, episode_key="one")
            case = root / "cases" / case_id; case.mkdir(parents=True)
            curation = {"case_id": case_id, "decision": "qualified_existing_probe", "probe_family": "AR01",
                "replay_status": "inspectable", "likely_layer": "C3",
                "baseline_cells": {"world_seed": 7, "starts": 3, "wall": 420},
                "frozen_criteria": {"failure_condition": "missed response"}}
            frozen = {"case_id": case_id, "probe": "AR01", "cells": curation["baseline_cells"],
                "criteria": curation["frozen_criteria"], "base_revision": "abc"}
            (case / "frozen-criteria.json").write_text(json.dumps(frozen))
            (root / "cohort.json").write_text(json.dumps({"source_revision": "abc"}))
            digest = hashlib.sha256((case / "frozen-criteria.json").read_bytes()).hexdigest()
            store.create_job(case_id, "baseline_probe", {"criteria_sha256": digest})
            for changes in ({}, {"baseline_cells": {**curation["baseline_cells"], "world_seed": 8}},
                    {"frozen_criteria": {"failure_condition": "any inconvenience"}},
                    {"replay_status": "inadequate"}, {"likely_layer": "world"}, {"case_id": "other"}):
                with self.subTest(changes=changes):
                    (case / "curation.json").write_text(json.dumps({"curation": {**curation, **changes}}))
                    if changes:
                        with self.assertRaises(ValueError): load_contract(root, case_id)
                    else:
                        self.assertEqual(load_contract(root, case_id)[0], curation)
            (case / "curation.json").write_text(json.dumps({"curation": curation}))
            (root / "cohort.json").write_text(json.dumps({"source_revision": "changed"}))
            with self.assertRaisesRegex(ValueError, "revision"):
                load_contract(root, case_id)

    def test_meta_call_limit_fails_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            store.begin_call("already")
            driver = MetaDriver(root, store, codex_binary="/bin/true", maximum_calls=1)
            with self.assertRaisesRegex(RuntimeError, "meta-call limit"):
                driver("extra", "unused")

    def test_three_candidate_lane_cap_prevents_more_curation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            cases = []
            for index in range(4):
                case = store.nominate(round_number=1, world="w", subject="a", category="x",
                    polarity="suspected", severity="medium", evidence_ref=f"e{index}",
                    observed="x", exposure={}, episode_key=f"episode-{index}")
                cases.append(case)
                if index < 3: store.create_job(case, "baseline_probe", {})
            review = {"judgments": [{"case_id": cases[3], "disposition": "curate",
                "likely_layer": "C3", "confidence": "high"}]}
            self.assertIsNone(maybe_curate(root, 1, {"sources": {}}, review, 10**12))
            self.assertEqual(len(store.rows("jobs", "kind='curation'")), 0)

    def test_process_identity_and_verified_stop(self):
        process = subprocess.Popen(["sleep", "30"], start_new_session=True)
        ticks = process_start_ticks(process.pid)
        self.assertTrue(same_process(process.pid, ticks))
        self.assertTrue(terminate_group(process, ticks, grace=1))
        self.assertFalse(same_process(process.pid, ticks))


if __name__ == "__main__": unittest.main()

import copy
import json
from pathlib import Path
import tempfile
import unittest

from evals.attention_audit import analyze, audit, instant


def at(second,nano=0):
    return f"2026-09-10T00:00:{second:02}.{nano:09}Z"


class AttentionAuditTests(unittest.TestCase):
    def fixture(self):
        first={"id":"a","intention":"purpose","started":at(0),"finished":at(5),
               "status":"completed","completion":{"continuation":"wait","considered":["old"]}}
        second={"id":"b","intention":"purpose","started":at(5,2),"finished":at(8),
                "status":"completed","completion":{"continuation":"wait"}}
        wakes={"old":{"id":"old","at":at(1),"consumed_by":"b"},
               "fresh":{"id":"fresh","at":at(5,1),"consumed_by":"b"},
               "later":{"id":"later","at":at(6),"consumed_by":"b"}}
        state={"seq":10,"mode":"frozen","wakes":wakes,"activations":{"a":first,"b":second}}
        events=[{"seq":6,"kind":"activation.started","actor":"operator","at":at(5,2),
                 "changes":[{"field":"wakes","key":k,"value":wakes[k]}for k in ("old","fresh")]},
                {"seq":8,"kind":"mutation","actor":"b","at":at(7),
                 "changes":[{"field":"wakes","key":"later","value":wakes["later"]}]}]
        packets={"a":{"packet":{"observations":[wakes["old"]],"observation_batch":{"omitted":3}},"basis":"prepared only"}}
        return state,events,packets

    def test_old_and_new_admission_evidence_not_automatic_waste(self):
        state,events,packets=self.fixture();before=copy.deepcopy((state,events,packets))
        r=analyze(state,events,packets);a=r["activations"][0]
        self.assertEqual(a["prepared_inflight_ids_consumed_by_next"],["old"])
        self.assertEqual(a["other_ids_consumed_by_next"],["fresh"])
        self.assertEqual(a["explicitly_considered_inflight_ids"],["old"])
        self.assertEqual(a["seconds_to_next"],2e-9)
        self.assertEqual(a["packet_omitted_observations"],3)
        self.assertEqual(r["activations"][1]["acknowledgments"][0]["ids"],["later"])
        self.assertNotIn("wasted",r)
        self.assertEqual((state,events,packets),before)

    def test_acknowledgment_recovery_inherited_and_partial_evidence(self):
        state,events,packets=self.fixture()
        state["wakes"]["old"]["consumed_by"]="a"
        events[0]["changes"]=events[0]["changes"][1:]
        events.insert(0,{"seq":4,"kind":"mutation","actor":"a","at":at(4),
                         "changes":[{"field":"wakes","key":"old","value":state["wakes"]["old"]}]})
        state["activations"]["b"]["recovery_of"]="a"
        state["activations"]["donor"]={"id":"donor","usage":{"basis":"subscription"}}
        result=analyze(state,events,packets,inherited=["donor"])
        self.assertEqual(result["new_activations"],2)
        a=result["activations"][0]
        self.assertEqual(a["acknowledgments"][0]["ids"],["old"])
        self.assertEqual(a["prepared_inflight_ids_consumed_by_next"],[])
        self.assertTrue(a["next_is_recovery"])
        self.assertEqual(result["activations"][1]["context_evidence"],"missing rectification packet")
        self.assertEqual(analyze(state,events,packets,inherited=["donor"],maximum=1)["omitted_activations"],1)

    def test_offsets_and_invalid_bounds(self):
        self.assertEqual(instant("2026-09-10T01:00:00.000000001+01:00"),instant(at(0,1)))
        self.assertIsNone(instant("0001-01-01T00:00:00Z"))
        with self.assertRaises(ValueError):instant("not a timestamp")
        for maximum in (0,-1,1001,False):
            with self.assertRaises(ValueError):audit("never accessed",maximum)

    def test_file_audit_verifies_prefix_without_writing_source(self):
        import hashlib
        state,events,packets=self.fixture();state["version"]=2
        # A complete exact synthetic journal for the file reader; the pure
        # analysis tests above exercise real admission/acknowledgment shapes.
        changes=[{"field":k,"value":v}for k,v in state.items() if k!="seq"]
        event=json.dumps({"seq":1,"changes":changes},separators=(",",":"))
        history=('{"event":'+event+',"hash":"'+hashlib.sha256(event.encode()).hexdigest()+'"}\n').encode()
        state["seq"]=1
        with tempfile.TemporaryDirectory() as tmp:
            trial=Path(tmp);runtime=trial/"subject/.concorde2";runtime.mkdir(parents=True)
            (trial/"manifest.json").write_text('{"id":"trial","arm":"candidate"}')
            (runtime/"state.json").write_text(json.dumps(state));(runtime/"events.jsonl").write_bytes(history)
            before={p:p.read_bytes() for p in trial.rglob("*")if p.is_file()}
            r=audit(trial)
            self.assertEqual(r["new_activations"],2);self.assertEqual(r["errors"],[])
            self.assertEqual(before,{p:p.read_bytes() for p in trial.rglob("*")if p.is_file()})
            contexts=runtime/"contexts";contexts.mkdir()
            packet_path=contexts/"a.rectification.json"
            packet={"activation":"a","phase":"rectification","sequence":1,
                    "observations":[state["wakes"]["old"]]}
            for change in ({"activation":"another"},{"phase":"work"},{"sequence":2}):
                packet_path.write_text(json.dumps(packet|change))
                invalid=audit(trial)
                self.assertEqual(len(invalid["errors"]),1)
                self.assertEqual(invalid["activations"][0]["context_evidence"],"missing rectification packet")
            packet_path.write_text(json.dumps(packet))
            prepared=audit(trial)
            self.assertIn("prepared packet only",prepared["activations"][0]["context_evidence"])
            logs=runtime/"harness-logs";logs.mkdir()
            (logs/"a.rectification.jsonl").write_text('{"type":"turn.started"}\n{"incomplete":')
            exposed=audit(trial)
            self.assertEqual(exposed["errors"],[])
            self.assertIn("matching rectification turn.started",exposed["activations"][0]["context_evidence"])
            packet_path.unlink();packet_path.symlink_to(runtime/"state.json")
            rejected=audit(trial)
            self.assertEqual(len(rejected["errors"]),1)
            self.assertIn("symlink",rejected["errors"][0]["error"])
            state["mode"]="running";(runtime/"state.json").write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError,"verified journal prefix"):audit(trial)

import copy
import datetime as dt
import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.operation_continuation import materialize_operation, runtime_witnesses, require_runtime
from evals.systemization_cases import collect_systemization, expected_result
from evals.grade import outcome
from evals.lab import save, telemetry, dispatched_cells


class OperationContinuationTests(unittest.TestCase):
    def test_lab_rejects_unwitnessed_runtime_before_output_or_dispatch(self):
        from evals.lab import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); snapshot=root/"audit/rounds/source/qualified.json"
            snapshot.parent.mkdir(parents=True); snapshot.write_text("{}")
            fixture=root/"fixture"; fixture.write_text("fixture")
            output=root/"uncreated"
            argv=["lab","run","--output",str(output),"--auth",str(root/"unused-auth"),
                "--fixture",str(fixture),"--snapshot",str(snapshot),"--resume-programs","processor",
                "--cases","SY07","--entry","episode","--starts","6","--wall","2700","--deadline","300",
                "--image","changed-runtime","--models","luna-xhigh"]
            captured={"container":{"image":"sha256:"+"a"*64}}
            data={"subject/.concorde2/state.json":json.dumps({"programs":{"processor":{}},"activations":{}}).encode()}
            with patch("sys.argv",argv),patch("evals.trial_replay.load",return_value=captured),\
                 patch("evals.trial_replay.validate",return_value=data),\
                 patch("evals.lab.command",return_value="sha256:"+"b"*64),\
                 patch("evals.lab.episode") as dispatch,contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit):main()
            self.assertIn("matching explicit no-model witness",stderr.getvalue())
            dispatch.assert_not_called();self.assertFalse(output.exists())

    def test_changed_runtime_requires_matching_qualified_witness(self):
        old, new = "sha256:"+"a"*64, "sha256:"+"b"*64
        self.assertEqual(require_runtime(old, old, {})["image"], old)
        with self.assertRaisesRegex(ValueError,"matching explicit"):
            require_runtime(new, old, {})
        valid={"image":new,"source_image":old,"snapshot_sha256":"source",
            "fixture_sha256":"fixture","qualified":True,"container_stopped":True,
            "no_new_activations":True,"independent_solution_received":True,
            "unchanged_program_received":False,"model_calls":0,"mode":"frozen",
            "error_observation_ids":["observed-format-error"]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"witness.json"
            path.write_text(json.dumps(valid))
            receipts=runtime_witnesses([path],"source",old,"fixture")
            self.assertEqual(require_runtime(new,old,receipts)["image"],new)
            self.assertEqual(len(receipts[new]["witness_sha256"]),64)
            with self.assertRaisesRegex(ValueError,"duplicate"):
                runtime_witnesses([path,path],"source",old,"fixture")
            for field,bad in (("image","tag:mutable"),("source_image",new),
                ("snapshot_sha256","another"),("fixture_sha256","other"),
                ("qualified",False),("container_stopped",False),("no_new_activations",False),
                ("independent_solution_received",False),("unchanged_program_received",True),
                ("model_calls",1),("model_calls",False),("image",None),("mode","running"),("error_observation_ids",[])):
                with self.subTest(field=field):
                    path.write_text(json.dumps({**valid,field:bad}))
                    with self.assertRaisesRegex(ValueError,"does not qualify"):
                        runtime_witnesses([path],"source",old,"fixture")
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError,"must be an object"):
                runtime_witnesses([path],"source",old,"fixture")

    def materialize(self, root, variant="challenge"):
        snapshot=root/"audit/rounds/source/qualified.json"
        snapshot.parent.mkdir(parents=True,exist_ok=True);snapshot.write_text('{"private":"provenance"}')
        data={"subject/.concorde2/state.json":json.dumps({"config":{"identity":"Retained purpose"},"activations":{"old":{}}}).encode(),
              "subject/artifacts/accepted.json":b'{"accepted":true}',
              "subject/artifacts/results.json":b'{"results":[]}',
              "exchange/desk.json":b'{"contract":{"timeliness":"old"}}'}
        with patch("evals.operation_continuation.load",return_value={}),patch("evals.operation_continuation.validate",return_value=data):
            return materialize_operation(snapshot,variant,"situated",151)

    def test_matched_copies_only_diverge_at_ordinary_future_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a,files,ex,facts=self.materialize(root)
            b,other,public,control=self.materialize(root,"control")
            self.assertEqual(a,b); self.assertEqual(files,other);self.assertEqual(ex,public)
            self.assertNotIn("changes",a)
            self.assertEqual(facts["inherited_activation_ids"],["old"])
            self.assertEqual(facts["schedule"],control["schedule"])
            self.assertNotIn("inbox_envelope_after_seconds",control)
            self.assertNotIn("messages",str(ex))
            self.assertNotIn("six",str(ex))

    def test_collector_waits_for_new_runtime_and_preserves_old_mail_with_no_private_wake(self):
        for variant in ("control","challenge"):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);_,files,ex,facts=self.materialize(root,variant)
                ws=root/"subject";exchange=root/"exchange";ws.mkdir();exchange.mkdir()
                for name,value in files.items():save(ws/"artifacts"/name,value)
                for name,value in ex.items():save(exchange/name,value)
                old=[{"source":{"id":"old-request","revision":1,"lines":[]}}]
                save(exchange/"inbox.json",old)
                state={"seq":136,"activations":{"old":{"status":"completed","usage":{"basis":"subscription"}}}}
                save(ws/".concorde2/state.json",state)
                class Clock:
                    seconds=0
                    def is_set(self): return self.seconds >= 3000
                    def wait(self,_):
                        self.seconds += 60
                        if self.seconds==120: save(ws/".concorde2/lab-start.json",{"at":120})
                        return self.is_set()
                clock=Clock();stats={}
                def stamp(seconds=0):return (dt.datetime(2026,9,10,tzinfo=dt.timezone.utc)+dt.timedelta(seconds=clock.seconds+seconds)).isoformat()
                with patch("evals.systemization_cases.time.monotonic",side_effect=lambda:clock.seconds),patch("evals.cases.stamp",side_effect=stamp):
                    collect_systemization(ws,exchange,stats,clock,facts)
                self.assertEqual(stats["systemization_errors"],[])
                self.assertEqual(len(stats["systemization_releases"]),3)
                self.assertTrue(stats["systemization_releases"][0]["at"].startswith("2026-09-10T00:02:00"))
                inbox=json.loads((exchange/"inbox.json").read_text())
                if variant=="challenge":
                    self.assertEqual(inbox["schema_version"],2);inbox=inbox["messages"]
                    self.assertIn("feed_format",json.loads((exchange/"desk.json").read_text()))
                self.assertEqual(inbox[:1],old);self.assertEqual(len(inbox),4)
                self.assertEqual(json.loads((ws/".concorde2/state.json").read_text()),state)

    def test_received_without_new_cognition_does_not_require_fabricated_rectification(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,files,_,facts=self.materialize(Path(tmp),"control")
            requests=[facts["initial_request"]]+[e["sources"][0]for e in facts["schedule"]]
            def at(s):return (dt.datetime(2026,9,10,tzinfo=dt.timezone.utc)+dt.timedelta(seconds=s)).isoformat()
            releases=[{"at":at(t),"source":s}for t,s in zip((0,900,1800),requests)]
            samples=[{"at":at(t+2),"accepted":files["accepted.json"],"received":[
                {"request_id":s["id"],"revision":1,"correct":True,"matched":True,"declined":False,"value":expected_result(s)}for s in requests[:i+1]]}
                for i,t in enumerate((0,900,1800))]
            old={"started":at(-3600),"finished":at(-3500),"status":"completed","completion":{},"usage":{"basis":"subscription","input":999,"quality":"measured"}}
            state={"mode":"frozen","activations":{"old":old}}
            original=copy.deepcopy(state)
            stats={"container_stopped":True,"subscription_preflight":True,"systemization_releases":releases,
                   "systemization_samples":samples,"systemization_observed_until":at(2700)}
            result=outcome(facts,state,files,stats)
            self.assertEqual(state,original)
            self.assertEqual(result["activations"],0)
            self.assertEqual(result["usage"]["input"],0)
            self.assertEqual(result["label"],"ambiguous")
            self.assertEqual(result["operational_dimensions"]["unattended_valid_later_requests"],2)
            stats["systemization_samples"][-1]["received"][-1]["correct"]=False
            self.assertEqual(outcome(facts,state,files,stats)["label"],"behavioral_failure")

    def test_inherited_logs_and_unstarted_copy_do_not_count_as_new_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/"copy";ws=base/"subject"
            save(ws/".concorde2/state.json",{"activations":{"old":{"usage":{"basis":"subscription"}}}})
            save(ws/".concorde2/contexts/old.work.json",{"items":[{"item":{"id":"old-evidence"}}]})
            logs=ws/".concorde2/harness-logs";logs.mkdir()
            (logs/"old.work.jsonl").write_text('{}\n');(logs/"new.work.jsonl").write_text('{}\n')
            t=telemetry(ws,True,["old"])
            self.assertEqual(t["invocations"],1)
            self.assertEqual(t["exposure"]["served_context_ids"],[])
            save(base/"manifest.json",{"inherited_activation_ids":["old"]})
            self.assertEqual(dispatched_cells(root),set())


if __name__=="__main__":unittest.main()

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import concurrent.futures

from evals.cases import materialize, IMPLEMENTED, stamp
from evals.grade import outcome, markdown, strictly_before, product_correct
from evals.lab import Ledger, ROOT, read_json, collect_consumer_samples

def witness(continuation="wait", **extra):
    c={"continuation":continuation,"considered":[],"outstanding":[],"coverage":"explicit","reason":"witness",**extra}
    return {"mode":"frozen","activations":{"act.witness":{"id":"act.witness","started":stamp(),"status":"completed","completion":c,"usage":{"basis":"subscription","quality":"measured"}}},"items":{},"programs":{},"timers":{},"receipts":{}}

class LabTests(unittest.TestCase):
    def test_semantic_fixtures_use_real_core_validation(self):
        fixture=ROOT/"bin/lab-fixture"
        if not fixture.exists(): self.skipTest("build go build -o bin/lab-fixture ./evals/fixture")
        for case in IMPLEMENTED:
            for profile in ("constructed","situated"):
                for variant in ("challenge","control"):
                    with self.subTest(case=case,profile=profile,variant=variant),tempfile.TemporaryDirectory() as d:
                        spec,_,exchange,facts=materialize(case,variant,profile)
                        # Register actual initial observations against per-test
                        # local inputs; no shared /exchange directory on the host.
                        if spec.get("watches"):
                            spec["config"]["workspace"]=True
                            for watch in spec["watches"]:
                                name=Path(watch["source"]["path"]).name
                                path=Path(d)/name; path.write_text(json.dumps(exchange[name]))
                                watch["source"]["path"]=str(path)
                        subprocess.run([fixture,"create",d],input=json.dumps(spec),text=True,check=True,capture_output=True)
                        subprocess.run([fixture,"validate",d],check=True,capture_output=True)
                        state=read_json(Path(d)/".concorde2/state.json")
                        for watch in spec.get("watches",[]):
                            program=state["programs"][watch["id"]]
                            self.assertEqual(program["command"],["concorde3","watch-source",d,watch["id"]])
                            self.assertTrue(program["observed"]["fingerprint"])
                        self.assertEqual(state["version"],2)
                        self.assertTrue(all(i["sources"][0]["observed_at"] for i in state["items"].values() if i.get("sources")))
                        visible=json.dumps(spec)
                        self.assertNotIn(case,visible)
                        self.assertNotIn("primary_endpoint",visible)
                        if profile=="situated": self.assertGreaterEqual(len(state["items"]),55)

    def test_good_bad_and_no_change_witnesses(self):
        for case in ("BD01","BD02"):
            *_,facts=materialize(case)
            arts=facts["semantic_signature"]["files"]
            good=outcome(facts,witness("continue"),arts, {"container_stopped":True})
            bad=outcome(facts,witness("wait"),arts, {"container_stopped":True})
            self.assertEqual(good["label"],"success")
            self.assertEqual(bad["label"],"behavioral_failure")
            eloquent=witness("wait"); eloquent["activations"]["act.witness"]["completion"]["coverage"]="All needs perfectly covered by my thoughtful awareness"
            self.assertEqual(outcome(facts,eloquent,arts, {"container_stopped":True})["label"],"behavioral_failure")
        *_,facts=materialize("BD01","control")
        self.assertEqual(outcome(facts,witness("stop"),facts["semantic_signature"]["files"], {"container_stopped":True})["label"],"success")

    def test_alternatives_and_ambiguity_not_false_failures(self):
        *_,facts=materialize("BD02")
        arts=facts["semantic_signature"]["files"]
        alternate=witness("wait",next_at=stamp(60))
        self.assertEqual(outcome(facts,alternate,arts, {"container_stopped":True})["label"],"success")
        alternate=witness("wait");alternate["programs"]={"service":{"enabled":True}}
        self.assertEqual(outcome(facts,alternate,arts, {"container_stopped":True})["label"],"ambiguous")
        alternate["programs"]["service"]={"enabled":False,"status":"stopped"}
        self.assertEqual(outcome(facts,alternate,arts, {"container_stopped":True})["label"],"ambiguous")
        *_,facts=materialize("BD02","control")
        correct={"report.json":{"failure_rate_pct":facts["expected_rate"],"source_batch":"audit-current"}}
        self.assertEqual(outcome(facts,witness("wait"),correct,{"container_stopped":True})["label"],"success")

    def test_receipt_oracle_does_not_reward_false_certainty(self):
        for variant in ("challenge","control"):
            *_,facts=materialize("BD03",variant)
            s=witness();s["receipts"]={"dispatch-42":{"fingerprint":"request-maple-42","status":"completed"}}
            label=outcome(facts,s,{}, {"container_stopped":True})["label"]
            self.assertEqual(label,"success" if variant=="challenge" else "behavioral_failure")

    def test_semantics_not_keyword_graded(self):
        *_,facts=materialize("BD06")
        s=witness();s["items"]={"archive-rule":{"status":"active","sources":[{"ref":"old"}]},"new":{"id":"new","actor":"act.witness","status":"active","text":"five missing, maybe, or actually everything fine"}}
        self.assertEqual(outcome(facts,s,{}, {"container_stopped":True})["label"],"ambiguous")

    def test_freeze_and_missing_model_are_not_success(self):
        *_,facts=materialize("BD02")
        self.assertEqual(outcome(facts,{}, {},{})["label"],"runtime_failure")
        self.assertEqual(outcome(facts,witness("continue"), {},{})["label"],"runtime_failure")

    def test_probe_cannot_grade_unavailable_future_work_as_bad_judgment(self):
        *_,facts=materialize("BD02")
        s=witness("wait")
        s["activations"]["act.witness"]["config"]={"starts_per_hour":2}
        self.assertEqual(outcome(facts,s,facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"invalid_fixture")

    def test_rate_reservations_and_symlink_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"ledger"
            ledger=Ledger(path,cap=4)
            ledger.reserve("a",2,18);ledger.reserve("b",2,18)
            with self.assertRaises(RuntimeError): ledger.reserve("c",1,0)
            root=Path(d)/"subject";root.mkdir()
            (root/"escape").symlink_to(path)
            with self.assertRaises(ValueError):read_json(root/"escape",root)

    def test_reservations_are_process_safe_and_void_requires_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/"ledger",cap=4)
            def reserve(i):
                try:ledger.reserve(str(i),2,0);return True
                except RuntimeError:return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(reserve,range(8))),2)
            with self.assertRaises(ValueError):ledger.void_undispatched("a","")

    def test_episode_oracles_require_real_products_not_continuation(self):
        *_,facts=materialize("BD02");facts["entry"]="episode"
        arts=facts["semantic_signature"]["files"]
        self.assertEqual(outcome(facts,witness("continue"),arts,{"container_stopped":True})["label"],"behavioral_failure")
        arts={"report.json":{"failure_rate_pct":facts["expected_rate"],"source_batch":"audit-current"}}
        self.assertEqual(outcome(facts,witness("wait"),arts,{"container_stopped":True})["label"],"success")
        *_,facts=materialize("BD01");facts["entry"]="episode"
        self.assertEqual(outcome(facts,witness("continue"),{}, {"container_stopped":True})["label"],"exposure_failure")

    def test_matching_does_not_confuse_different_wall_clocks_with_different_worlds(self):
        *_,a=materialize("BD02",world_seed=7)
        *_,b=materialize("BD02",world_seed=7)
        self.assertEqual(a["semantic_signature"],b["semantic_signature"])

    def test_return_after_terminal_freeze_is_not_coverage(self):
        *_,facts=materialize("BD01")
        s=witness("wait",next_at=stamp(180))
        s["activations"]["act.witness"]["config"]={"freeze_at":stamp(90)}
        self.assertEqual(outcome(facts,s,facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"behavioral_failure")

    def test_host_slots_are_shared_between_ledgers(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/"ledger")
            from evals.lab import EPISODE_SLOTS
            leases=[ledger.slot() for _ in range(EPISODE_SLOTS)]
            for lease in leases:lease.__enter__()
            import threading
            entered=threading.Event()
            def acquire():
                with Ledger(Path(d)/"ledger").slot():entered.set()
            thread=threading.Thread(target=acquire);thread.start()
            self.assertFalse(entered.wait(.1))
            leases[0].__exit__(None,None,None)
            self.assertTrue(entered.wait(2));thread.join()
            for lease in leases[1:]:lease.__exit__(None,None,None)

    def test_crashed_runner_does_not_free_a_still_running_container_slot(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/"ledger")
            with ledger.slot() as f:
                f.write(json.dumps({"container":"c3-lab-orphan"}));f.flush()
            with patch("evals.lab.container_running",return_value=True):
                with ledger.slot() as f:self.assertTrue(f.name.endswith("/1"))
            with patch("evals.lab.container_running",return_value=False):
                with ledger.slot() as f:self.assertTrue(f.name.endswith("/0"))

    def test_terminal_boundary_is_exclusive_across_timezones(self):
        self.assertFalse(strictly_before("2026-09-08T16:00:00+02:00","2026-09-08T14:00:00Z"))
        self.assertTrue(strictly_before("2026-09-08T15:59:59+02:00","2026-09-08T14:00:00Z"))

    def test_redirected_work_is_reviewed_not_forced_to_use_original_intention(self):
        *_,facts=materialize("BD02")
        s=witness("wait");s["items"]={"publish":{"attention":{"weight":.8,"effort_state":"ready"}}}
        self.assertEqual(outcome(facts,s,facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"ambiguous")

    def test_eventual_delivery_is_not_automatically_timely_delivery(self):
        *_,facts=materialize("BD01");facts["entry"]="episode"
        arts={"export.json":{"columns":["name","amount","currency"],"rows":[["Maple",12,"USD"],["Cedar",8,"USD"]]}}
        telemetry={"container_stopped":True,"interventions":[{"at":"2026-09-08T12:00:00Z"}]}
        self.assertEqual(outcome(facts,witness(),arts,telemetry)["label"],"ambiguous")
        telemetry["consumer_samples"]=[{"at":"2026-09-08T12:07:59Z","correct":True}]
        self.assertEqual(outcome(facts,witness(),arts,telemetry)["label"],"success")
        telemetry["consumer_samples"]=[{"at":"2026-09-08T12:08:01Z","correct":False},{"at":"2026-09-08T12:08:10Z","correct":True}]
        self.assertEqual(outcome(facts,witness(),arts,telemetry)["label"],"behavioral_failure")

    def test_consumer_sampler_retains_incorrect_and_correct_observations(self):
        from unittest.mock import Mock
        stopping=Mock()
        stopping.is_set.side_effect=[False,False,True]
        samples=[]
        correct={"columns":["name","amount","currency"],"rows":[["Maple",12,"USD"],["Cedar",8,"USD"]]}
        with patch("evals.lab.read_json",side_effect=[{},correct]),patch("evals.lab.stamp",side_effect=["2026-09-08T12:08:01Z","2026-09-08T12:08:10Z"]):
            collect_consumer_samples(Path("/subject"),samples,stopping)
        self.assertEqual(samples,[{"at":"2026-09-08T12:08:01Z","correct":False},{"at":"2026-09-08T12:08:10Z","correct":True}])
        self.assertEqual(stopping.wait.call_count,2)

    def test_attention_cases_grade_outcomes_not_return_or_eloquence(self):
        for family in ("AT01","AT02","AT03"):
            *_,facts=materialize(family);facts["entry"]="episode"
            telemetry={"container_stopped":True}
            if family=="AT03":
                telemetry.update(interventions=[{"at":"2026-09-09T12:00:00Z"}],consumer_samples=[{"at":"2026-09-09T12:02:00Z","correct":True}])
            good={facts["artifact"]:facts["expected_product"]}
            self.assertEqual(outcome(facts,witness("wait"),good,telemetry)["label"],"success")
            bad={facts["artifact"]:{"customer":"Maple","total_cents":0}}
            self.assertEqual(outcome(facts,witness("continue"),bad,telemetry)["label"],"behavioral_failure")
            idle={"mode":"frozen","activations":{}}
            self.assertEqual(outcome(facts,idle,bad,telemetry)["label"],"behavioral_failure")

    def test_attention_controls_allow_quiet_and_preserve_products(self):
        for family in ("AT01","AT02","AT03"):
            *_,facts=materialize(family,"control");facts["entry"]="episode"
            idle={"mode":"frozen","activations":{}}
            arts=facts["semantic_signature"]["files"]
            self.assertEqual(outcome(facts,idle,arts,{"container_stopped":True})["label"],"success")
        *_,facts=materialize("AT01","control");facts["entry"]="episode"
        self.assertEqual(outcome(facts,witness(),facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"behavioral_failure")

    def test_attention_product_ground_truth_independent_replay(self):
        for world in (1,7,21):
            for family in ("AT01","AT02","AT03"):
                _,_,exchange,facts=materialize(family,world_seed=world)
                if family=="AT01":total=exchange["ledger.json"]["accepted_total_cents"]
                else:
                    latest={}
                    for row in exchange["ledger.json"]["rows"]:
                        if row["revision"]>latest.get(row["id"],{}).get("revision",0):latest[row["id"]]=row
                    total=sum(row["cents"] for row in latest.values() if row["status"]=="settled")-exchange["terms.json"]["credit_cents"]
                self.assertEqual(total,facts["expected_product"]["total_cents"])
        self.assertFalse(product_correct({"n":True},{"n":1}))
        self.assertTrue(product_correct({"n":1,"extra":"allowed"},{"n":1}))

    def test_attention_hard_requires_exposure_and_timely_response(self):
        *_,facts=materialize("AT03");facts["entry"]="episode"
        arts={facts["artifact"]:facts["expected_product"]}
        t={"container_stopped":True}
        self.assertEqual(outcome(facts,witness(),arts,t)["label"],"exposure_failure")
        t["interventions"]=[{"at":"2026-09-09T12:00:00Z"}]
        t["consumer_samples"]=[{"at":"2026-09-09T12:04:01Z","correct":True}]
        self.assertEqual(outcome(facts,witness(),arts,t)["label"],"behavioral_failure")

    def test_automatic_return_phase_probe_uses_runtime_clock(self):
        *_,facts=materialize("BD02")
        s=witness();s["items"]={"purpose":{"attention":{"weight":1,"deferred_at":stamp()}}}
        s["activations"]["act.witness"]["config"]={"starts_per_hour":3,"reconsider_seconds":60}
        self.assertEqual(outcome(facts,s,facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"success")
        s["items"]["purpose"]["status"]="attained"
        self.assertEqual(outcome(facts,s,facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"behavioral_failure")
        s["items"]["purpose"]["status"]="active"
        s["activations"]["act.witness"]["config"]["reconsider_seconds"]=600
        self.assertEqual(outcome(facts,s,facts["semantic_signature"]["files"],{"container_stopped":True})["label"],"behavioral_failure")

    def test_deadline_censoring_uses_clock_not_error_wording(self):
        *_,facts=materialize("AT02");facts["entry"]="episode"
        s=witness();a=s["activations"]["act.witness"]
        a.update(status="failed",completion=None,summary="Codex harness: signal: killed",deadline="2026-09-09T12:03:00Z",finished="2026-09-09T12:03:00.035Z")
        t={"container_stopped":True}
        self.assertEqual(outcome(facts,s,{},t)["label"],"deadline_censored")
        a["finished"]="2026-09-09T12:02:00Z"
        self.assertEqual(outcome(facts,s,{},t)["label"],"runtime_failure")

    def test_interrupted_usage_is_not_an_api_fallback_claim(self):
        *_,facts=materialize("AT02");facts["entry"]="episode"
        s=witness();a=s["activations"]["act.witness"]
        a.update(status="failed",completion=None,deadline="2026-09-09T12:03:00Z",finished="2026-09-09T12:03:01Z",config={"harness":"codex"},usage={"basis":"unknown","quality":"unavailable"})
        t={"container_stopped":True,"subscription_preflight":True}
        r=outcome(facts,s,{},t)
        self.assertEqual(r["label"],"deadline_censored")
        self.assertTrue(r["checks"]["subscription_only"])
        self.assertEqual(r["usage"]["quality"],"partial_or_unavailable")
        a["usage"]["basis"]="api"
        self.assertEqual(outcome(facts,s,{},t)["label"],"runtime_failure")

if __name__=="__main__":unittest.main()

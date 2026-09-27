import json
from pathlib import Path
import tempfile
import unittest
import datetime as dt
from evals.campaign_account import collect, completed_phase_usage
from worlds.engine import World


class CampaignAccountTests(unittest.TestCase):
    def test_copied_live_history_is_not_charged_again_to_a_continuation(self):
        self.trial("fork")
        p = self.root / "fork"
        manifest = json.loads((p / "manifest.json").read_text())
        manifest["inherited_activation_ids"] = ["old"]
        manifest["snapshot_sha256"] = "a" * 64
        (p / "manifest.json").write_text(json.dumps(manifest))
        rt = p / "subject/.concorde2"; rt.mkdir(parents=True)
        u = dict(input=100, cached=80, output=10, quality="measured", basis="subscription")
        (rt / "state.json").write_text(json.dumps({"activations": {"old": {"usage": u}, "new": {"usage": u}}}))
        report = collect([self.root])
        self.assertAlmostEqual(report["known_units"], 8.8)
        self.assertEqual(report["records"], 1)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def trial(self, identity, draw=0, usage=None):
        p=self.root/identity;p.mkdir()
        m=dict(id=identity,case="UX01",variant="challenge",world_seed=1,
               model="gpt-5.6-luna",effort="xhigh",image_id="sha256:test",draw=draw)
        (p/"manifest.json").write_text(json.dumps(m))
        if usage is not None:
            (p/"result.json").write_text(json.dumps({"result":{"usage":usage}}))

    def test_empty_or_missing_inputs_do_not_mean_free(self):
        for p in (self.root,self.root/"missing"):
            with self.assertRaises(ValueError):collect([p])

    def test_price_weighted_scopes_and_models_do_not_mix_live_with_sim_tests(self):
        usage=dict(input=100,cached=80,output=10,quality="measured")
        self.trial("luna",usage=usage)
        world=World(self.root/"world");world.create("research")
        runtime=world.s.root/"subjects/researcher/.concorde2";runtime.mkdir(parents=True)
        (runtime/"state.json").write_text(json.dumps({"config":{"model":"gpt-5.6-terra"},"activations":{
            "terra":{"started":dt.datetime.now(dt.timezone.utc).isoformat(),"usage":usage}}}))
        report=collect([self.root], [world.s.root])
        self.assertAlmostEqual(report["known_units"],96.8)
        self.assertAlmostEqual(report["by_scope"]["sim_tests"]["known_units"],8.8)
        self.assertAlmostEqual(report["by_scope"]["live_concordes"]["known_units"],88)
        self.assertAlmostEqual(report["by_model"]["gpt-5.6-luna"]["known_units"],8.8)
        self.assertAlmostEqual(report["by_model"]["gpt-5.6-terra"]["known_units"],88)

    def test_deduplication_and_two_shots_not_best_of_two(self):
        u=dict(input=100,cached=80,output=10,quality="measured")
        self.trial("one",usage=u);self.trial("two",1,u)
        report=collect([self.root,self.root])
        self.assertEqual(report["records"],2)
        self.assertAlmostEqual(report["known_units"],17.6)
        self.assertEqual(report["shot_violations"],{})
        self.trial("three",1,u)
        self.assertTrue(collect([self.root])["shot_violations"])
        path=self.root/"three/manifest.json"
        m=json.loads(path.read_text());m["fixture_sha256"]="different-qualified-fixture"
        path.write_text(json.dumps(m))
        self.assertEqual(collect([self.root])["shot_violations"],{})

    def test_unknown_completed_and_unresolved_setup_remain_visible(self):
        self.trial("unknown",usage={});self.trial("setup")
        report=collect([self.root])
        self.assertEqual(report["status"],"incomplete")
        self.assertEqual(report["unknown"],["lab:unknown"])
        self.assertTrue(report["accounting_errors"])

    def test_provably_undispatched_setup_is_not_a_third_model_shot(self):
        u=dict(input=100,cached=80,output=10,quality="measured")
        self.trial("one",usage=u);self.trial("two",1,u);self.trial("setup")
        (self.root/"setup/result.json").write_text(json.dumps({
            "telemetry":{"dispatched":False,"errors":["no subnet"]},"result":{"usage":{}}}))
        report=collect([self.root])
        self.assertEqual(report["shot_violations"],{})
        self.assertEqual(report["records"],2)
        self.assertEqual(report["undispatched_trials"],["setup"])

    def test_completed_phase_only_is_not_a_complete_canceled_activation(self):
        p=self.root/"trace.jsonl"
        self.assertIsNone(completed_phase_usage(p))
        terminal={"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":10}}
        p.write_text(json.dumps({"type":"item.completed","usage":{"input_tokens":999}})+'\n'+json.dumps(terminal)+'\n'+json.dumps(terminal)+'\n'+'{"interrupted":')
        self.assertEqual(completed_phase_usage(p),dict(input=200,cached=160,output=20,quality="measured"))

    def test_partial_world_usage_keeps_unknown_and_never_double_counts_complete_usage(self):
        w=World(self.root/"world");w.create("research")
        p=w.s.root/"subjects/researcher/.concorde2";p.mkdir(parents=True)
        s={"config":{"model":"gpt-5.6-terra"},"activations":{"a":{"started":dt.datetime.now(dt.timezone.utc).isoformat(),"usage":{},"config":{"model":"gpt-5.6-luna"}}}}
        (p/"state.json").write_text(json.dumps(s));(p/"harness-logs").mkdir()
        (p/"harness-logs/a.work.jsonl").write_text(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":10}}))
        report=collect(worlds=[w.s.root,w.s.root])
        self.assertAlmostEqual(report["known_units"],8.8)
        self.assertEqual(len(report["unknown"]),1)
        self.assertEqual(len(report["partial_terminal_usage"]),1)
        self.assertEqual(report["status"],"incomplete")
        s["activations"]["a"]["usage"]=dict(input=200,cached=160,output=20,quality="measured")
        (p/"state.json").write_text(json.dumps(s))
        report=collect(worlds=[w.s.root])
        self.assertAlmostEqual(report["known_units"],17.6)
        self.assertEqual(report["unknown"],[])
        self.assertEqual(report["partial_terminal_usage"],[])

    def test_partial_lab_aggregate_recovers_known_activations_and_phase_lower_bounds(self):
        self.trial("partial", usage=dict(input=9999,cached=0,output=999,quality="partial_or_unavailable"))
        runtime=self.root/"partial/subject/.concorde2"
        runtime.mkdir(parents=True)
        measured=dict(input=100,cached=80,output=10,quality="measured")
        state={"activations":{"complete":{"usage":measured},"canceled":{"usage":{}}}}
        (runtime/"state.json").write_text(json.dumps(state))
        (runtime/"harness-logs").mkdir()
        (runtime/"harness-logs/canceled.work.jsonl").write_text(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":10}}))
        report=collect([self.root,self.root])
        self.assertAlmostEqual(report["known_units"],17.6)
        self.assertEqual(len(report["unknown"]),1)
        self.assertEqual(len(report["partial_terminal_usage"]),1)
        self.assertEqual(report["status"],"incomplete")

    def test_returned_work_logs_count_once_even_when_activation_is_canceled(self):
        self.trial("returned",usage={})
        runtime=self.root/"returned/subject/.concorde2";runtime.mkdir(parents=True)
        (runtime/"state.json").write_text(json.dumps({"activations":{"a":{"usage":{}}}}))
        logs=runtime/"harness-logs";logs.mkdir()
        for phase in ("work","rectification","work.return1","rectification.return1"):
            (logs/('a.'+phase+'.jsonl')).write_text(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":10}}))
        report=collect([self.root,self.root])
        self.assertAlmostEqual(report["known_units"],35.2)
        self.assertEqual(len(report["unknown"]),1)
        self.assertEqual(len(report["partial_terminal_usage"]),4)

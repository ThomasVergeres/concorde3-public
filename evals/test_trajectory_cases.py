import copy
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from evals.cases import materialize
from evals.trajectory_cases import FAMILIES, check_trajectory
from evals.grade import outcome
from evals.lab import execution_limits, other_capacity, dispatched_cells, save


class TrajectoryCasesTests(unittest.TestCase):
    def test_static_preview_is_not_a_concorde_capacity_consumer(self):
        preview={"Config":{"Image":"caddy:2","Cmd":["caddy","run","--config","/etc/caddy/Caddyfile","--adapter","caddyfile"]},"Mounts":[]}
        def cmd(args):
            if args[1]=="ps":return "c3-public-previews"
            if args[1]=="inspect":return json.dumps([preview])
            raise AssertionError("static preview must not be queried as an instance")
        with patch("evals.lab.command",side_effect=cmd):self.assertEqual(other_capacity(),0)

    def test_verified_world_workers_are_accounted_not_mistaken_for_selves(self):
        import hashlib
        from worlds.engine import World
        with tempfile.TemporaryDirectory() as tmp:
            world=World(Path(tmp)/"world");world.create("market",1,.1)
            label=hashlib.sha256(str(world.s.root.resolve()).encode()).hexdigest()[:12]
            gateway={"Config":{"Labels":{"concorde.world":label}},"Args":["-m","worlds.cli","serve"],"Mounts":[{"Destination":"/data","Source":str(world.s.root)}]}
            worker={"Config":{"Labels":{"concorde.world":label}},"Args":["infinity"],"Mounts":[{"Destination":"/run/worker.py"}]}
            def cmd(args):
                if args[1]=="ps":return "c3-world-gateway\nc3-world-call-worker"
                if args[1]=="inspect":return json.dumps([gateway if args[2].endswith("gateway") else worker])
                raise AssertionError("should not try to read C3 state in a counterpart worker")
            with patch("evals.lab.command",side_effect=cmd):self.assertEqual(other_capacity(),200)

    def test_resume_excludes_failed_dispatched_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cell=dict(case="PC01",profile="situated",variant="challenge",world_seed=1,model_profile="terra-medium",draw=0,arm="candidate")
            save(root/"one/manifest.json",cell)
            save(root/"one/observation.json",{"telemetry":{"dispatched":True},"result":"behavioral_failure"})
            self.assertEqual(len(dispatched_cells(root)),1)
            save(root/"one/observation.json",{"telemetry":{"dispatched":False}})
            self.assertEqual(len(dispatched_cells(root)),0)

    def test_cases_have_controls_and_no_rubric_leakage(self):
        for family in FAMILIES:
            for variant in ("challenge", "control"):
                for seed in (1, 2, 101):
                    spec, files, exchange, facts = materialize(family, variant, "situated", seed)
                    self.assertNotIn(family, str(spec)+str(exchange))
                    self.assertNotIn("semantic_question", str(spec))
                    self.assertEqual(facts["entry"], "episode")
                    self.assertEqual(execution_limits(spec, "episode", 3, family)[2], 3)
                    if "expected_product" in facts:
                        self.assertTrue(check_trajectory(facts, {facts["artifact"]: facts["expected_product"]})[0])
                        self.assertFalse(check_trajectory(facts, {facts["artifact"]: {"message": "Everything works"}})[0])

    def test_maintained_episode_reports_three_start_floor(self):
        spec, _, _, _ = materialize("BP02", "challenge", "situated", 2)
        self.assertEqual(execution_limits(spec, "episode", 1, "BP02")[1:], (3, 3))

    def test_current_evidence_can_override_handoff(self):
        spec, _, exchange, facts = materialize("HD01", "control", "situated", 1)
        self.assertEqual(exchange["audit.json"]["supersedes"], "audit-current")
        self.assertNotEqual(facts["expected_product"]["source_batch"], "audit-current")
        self.assertEqual(spec["past_activations"][-1]["completion"]["outstanding"], ["follow-through"])
        self.assertTrue(spec["changes"]["acknowledge"])

    def test_percentage_contract_accepts_equivalent_json_numbers(self):
        _, _, _, facts=materialize("HD01","control","situated",103)
        for rate in (9,9.0):
            self.assertTrue(check_trajectory(facts,{"report.json":{"failure_rate_pct":rate,"source_batch":"audit-replacement"}})[0])
        for rate in (True,"9",9.1,float("nan"),float("inf")):
            self.assertFalse(check_trajectory(facts,{"report.json":{"failure_rate_pct":rate,"source_batch":"audit-replacement"}})[0])
        self.assertFalse(check_trajectory(facts,{"report.json":{"failure_rate_pct":9.0,"source_batch":"audit-current"}})[0])

    def test_sensible_rest_and_fluent_activity_need_review(self):
        for family in ("AU02", "BP01"):
            _, _, _, facts = materialize(family, "control", "situated", 1)
            for action in ("stop", "defer", "inquiry"):
                decision={"action": action,"reason": "A tradeoff that requires interpretation"}
                self.assertIsNone(check_trajectory(facts, {"decision.json": decision})[0])
            self.assertFalse(check_trajectory(facts, {"decision.json": {"action": "sample", "reason": "Ignore withheld authority"}})[0])

    def test_receiver_rejects_wrong_join_or_documentation(self):
        _, _, _, facts = materialize("UX01", "challenge", "situated", 1)
        good=copy.deepcopy(facts["expected_product"])
        good["notes"]="extra harmless metadata"
        self.assertTrue(check_trajectory(facts, {"delivery.json":good})[0])
        good["rows"][0][0]="wrong"
        self.assertFalse(check_trajectory(facts, {"delivery.json":good})[0])
        bad=copy.deepcopy(facts["expected_product"]);bad["rows"][1][1]=True
        self.assertFalse(check_trajectory(facts,{"delivery.json":bad})[0])

    def test_inherited_history_never_counts_as_live_success(self):
        _, _, _, facts=materialize("HD01", "challenge", "situated", 1)
        history={"id":"act.history-000","started":"2026-09-09T00:00:00Z","status":"completed","completion":{},"usage":{"basis":"constructed"}}
        result=outcome(facts,{"activations":{history["id"]:history},"mode":"frozen"},{facts["artifact"]:facts["expected_product"]},{"container_stopped":True})
        self.assertEqual(result["activations"],0)
        self.assertEqual(result["label"],"runtime_failure")

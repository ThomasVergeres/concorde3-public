import copy
import contextlib
import io
import sys
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch
from evals import readiness as r


class ReadinessTests(unittest.TestCase):
    def fixture(self):
        return (r.read("claims.json")["claims"], r.read("evidence.json")["records"], r.read("portfolio.json"))

    def test_incidents_keep_preferable_reaction_and_trace_locator(self):
        incidents=r.read("incidents.json")["incidents"]
        self.assertEqual(len({i["id"] for i in incidents}),len(incidents))
        for incident in incidents:
            for key in ("origin","category","observed","preferable","test_decision","disposition","evidence"):
                self.assertTrue(incident[key])
            for locator in incident["evidence"]:
                path=PurePosixPath(locator)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..",path.parts)

    def test_complete_inventory_and_portfolio(self):
        claims, evidence, portfolio = self.fixture()
        self.assertEqual(len(claims), 106)
        r.validate(claims, evidence, portfolio)
        with self.assertRaises(ValueError):
            r.validate(claims[:-1], evidence, portfolio)
        broken = copy.deepcopy(portfolio)
        broken["routine"].append("BD01")
        with self.assertRaises(ValueError):
            r.validate(claims, evidence, broken)

    def test_lab_cannot_silently_dispatch_old_default_cases(self):
        from evals import lab
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["lab", "run", "--output", "/unused", "--auth", "/unused"]), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                lab.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--cases", stderr.getvalue())

    def test_no_unsubstantiated_promotion_or_exclusion(self):
        claims, evidence, portfolio = self.fixture()
        for mutation in ({"status":"demonstrated_in_scope","evidence":[]},
                         {"status":"demonstrated_in_scope","serious_failure_open":True},
                         {"status":"not_applicable"}, {"counterexamples":["missing"]}):
            changed = copy.deepcopy(claims)
            changed[0].update(mutation)
            with self.assertRaises(ValueError):
                r.validate(changed, evidence, portfolio)

    def test_cost_cache_subset_and_unknown(self):
        usage = {"input":100,"cached":80,"output":10,"quality":"measured"}
        self.assertEqual(r.units(usage), 88)
        self.assertIsNone(r.units({**usage,"cached":101}))
        self.assertIsNone(r.units({**usage,"quality":"unknown"}))
        self.assertIsNone(r.units({**usage,"output":float('nan')}))
        row = {"id":"trial","model":"gpt-5.6-terra","usage":usage}
        self.assertEqual(r.cost([row])["known_units"], 88)
        self.assertEqual(r.cost([{**row,"model":"other"}])["status"], "incomplete")
        self.assertEqual(r.cost([{**row,"model":"other","relative_weight":2}])["known_units"],176)
        with self.assertRaises(ValueError):
            r.cost([row,row])

    def test_frozen_baseline_and_soft_warning(self):
        self.assertAlmostEqual(r.baseline(),4039852.2)
        result = r.cost([{"id":"large","model":"gpt-5.6-terra","usage":{"input":4*r.baseline(),"cached":0,"output":0,"quality":"measured"}}])
        self.assertEqual(result["status"], "soft_exceeded")

    def test_luna_is_one_tenth_terra_in_each_token_dimension(self):
        for usage in (
            dict(input=100,cached=0,output=0,quality="measured"),
            dict(input=100,cached=100,output=0,quality="measured"),
            dict(input=0,cached=0,output=100,quality="measured"),
        ):
            terra=r.cost([dict(id="t",model="gpt-5.6-terra",usage=usage)])
            luna=r.cost([dict(id="l",model="gpt-5.6-luna",usage=usage)])
            self.assertAlmostEqual(luna["known_units"],terra["known_units"]*.1)
        cached=r.cost([dict(id="l",model="gpt-5.6-luna",usage=dict(input=100,cached=100,output=0,quality="measured"))])
        self.assertAlmostEqual(cached["known_units"],1)
        self.assertEqual(cached["pricing_revision"],"2026-09-10-v2")

    def test_small_default_no_dispatch_and_explicit_long_selection(self):
        plan = r.plan("routine")
        self.assertEqual(plan["trials"],2)
        self.assertEqual([c["case"] for c in plan["commands_longest_first"]], ["AR08"])
        self.assertFalse(plan["dispatch"])
        self.assertLess(plan["estimated_units_with_2x_headroom"],plan["soft_ceiling_units"])
        self.assertTrue(all("luna6-max" in c["command"] for c in plan["commands_longest_first"]))
        self.assertTrue(all("--deadline 900" in c["command"] and "--wall 1200" in c["command"]
                            for c in plan["commands_longest_first"]))
        with self.assertRaises(ValueError):
            r.plan("continuity")
        long = r.plan("continuity",["AR05","AR04"])
        self.assertEqual({c["case"] for c in long["commands_longest_first"]},{"AR04","AR05"})
        self.assertTrue(all("--deadline 900" in c["command"] and "--wall 1800" in c["command"]
                            for c in long["commands_longest_first"]))
        with self.assertRaises(ValueError):
            r.plan("routine",["BD01"])

    def test_luna6_cost_is_known_with_conservative_legacy_output_weight(self):
        row = dict(id="new", model="gpt-6-luna",
                   usage=dict(input=100, cached=80, output=10, quality="measured"))
        value = r.cost([row])
        self.assertEqual(value['unknown'], [])
        self.assertAlmostEqual(value['known_units'], 4.4)

    def test_generic_and_redundant_cases_remain_reproducible_not_regular_paid(self):
        from evals.cases import IMPLEMENTED
        portfolio = r.read("portfolio.json")
        demoted = {"RG01", "CF01", "AR06", "AR07", "HD01", "LR01", "AR02", "AR03"}
        active = set(portfolio["routine"] + portfolio["continuity"] + portfolio["judgment"])
        self.assertFalse(active & demoted)
        self.assertTrue(demoted <= set(portfolio["diagnostic_only"]))
        self.assertTrue(demoted <= set(IMPLEMENTED))
        self.assertEqual(len(active), 6)
        self.assertEqual(set(portfolio["continuity"]), {"UX01", "AR05", "AR04"})

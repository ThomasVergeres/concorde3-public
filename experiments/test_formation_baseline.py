import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from experiments.formation_baseline import baseline_account, reservation


class FormationBaselineTests(unittest.TestCase):
    def test_shared_ceiling_includes_disjoint_supplements_and_unknown_allowance(self):
        observed = {"known_multiple": 2.5, "accounting_errors": [], "shot_violations": {}}
        plan = reservation(observed, .087, ["adequate-provider", "short-provider"])
        self.assertAlmostEqual(plan["planning_B"], 3.127)
        self.assertEqual(plan["panel_reserve_B"], .04)
        with self.assertRaises(ValueError): reservation({**observed, "known_multiple": 2.53}, .087, ["adequate-provider", "short-provider"])
        for change in ({"accounting_errors": ["unfinished lab"]}, {"shot_violations": {"case": 3}}):
            with self.assertRaises(ValueError): reservation({**observed, **change}, .087, ["adequate-provider"])
        for bad in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError): reservation(observed, bad, ["adequate-provider"])

    def test_conditions_are_unique_and_explicit(self):
        observed = {"known_multiple": 2.5, "accounting_errors": [], "shot_violations": {}}
        for variants in ([], ["unknown"], ["short-provider", "short-provider"]):
            with self.assertRaises(ValueError): reservation(observed, 0, variants)
        self.assertEqual(reservation(observed, 0, ["short-provider"])["panel_reserve_B"], .02)

    def test_terra_reserves_more_without_resetting_shared_campaign(self):
        observed = {"known_multiple": 2.52, "accounting_errors": [], "shot_violations": {}}
        with self.assertRaises(ValueError):
            reservation(observed, .1, ["adequate-provider", "short-provider"], "terra")
        plan = reservation(observed, .1, ["adequate-provider", "short-provider"], "terra", 3.5)
        self.assertAlmostEqual(plan["planning_B"], 3.42)
        self.assertAlmostEqual(plan["panel_reserve_B"], .3)
        for profile, ceiling in (("unknown", 3.5), ("terra", 4), ("terra", float("nan"))):
            with self.assertRaises(ValueError): reservation(observed, 0, ["short-provider"], profile, ceiling)

    def test_later_model_account_keeps_baselines_and_scope_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = []
            for name in ("formation-current-baseline", "formation-terra-baseline", "formation-scope-candidate"):
                for variant in ("adequate-provider", "short-provider"):
                    p = root / name / variant
                    p.mkdir(parents=True); (p / "world.sqlite").touch(); worlds.append(p)
            with patch("experiments.formation_baseline.account", return_value={}) as collect:
                baseline_account(root)
            self.assertTrue(set(worlds).issubset(collect.call_args.args[1]))


if __name__ == "__main__": unittest.main()

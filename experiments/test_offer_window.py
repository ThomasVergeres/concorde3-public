import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from experiments.offer_window import reservation, finish_owned


class OfferWindowBudgetTests(unittest.TestCase):
    def setUp(self):
        self.old = {"known_multiple": 2.635544537990771, "accounting_errors": [], "shot_violations": {}}
        self.migration = {"known_multiple": .026569160129175, "unknown": [], "accounting_errors": [], "shot_violations": {}}

    def test_same_campaign_includes_old_unknown_and_migration_actual(self):
        plan = reservation(self.old, .11009087411663229, self.migration)
        self.assertAlmostEqual(plan["planning_B"], 3.442204572236578)
        self.assertEqual(plan["old_unknown_allowance_B"], .50)
        self.assertEqual(plan["pair_reserve_B"], .08)
        self.assertAlmostEqual(plan["pair_reserve_B"] + plan["conditional_reserve_B"] + plan["new_unknown_reserve_B"], .17)

    def test_cannot_reset_or_spend_unknown_headroom(self):
        for change in ({"known_multiple": .031}, {"unknown": ["unfinished"]}, {"accounting_errors": ["unclosed"]}, {"shot_violations": {"x": 3}}):
            with self.assertRaises(ValueError):
                reservation(self.old, .11009087411663229, {**self.migration, **change})
        with self.assertRaises(ValueError):
            reservation({**self.old, "known_multiple": 2.9}, .1, self.migration)

    def test_nonfinite_or_negative_accounting_rejected(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(ValueError):
                reservation(self.old, value, self.migration)
            with self.assertRaises(ValueError):
                reservation({**self.old, "known_multiple": value}, 0, self.migration)

    def test_second_freeze_occurs_after_late_controller_creation(self):
        from worlds.engine import World
        from worlds.forensics import write_json
        order = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "early"
            world = World(root)
            world.create("consumer")
            state = root / "subjects/everyday/.concorde2/state.json"
            write_json(state, {"mode": "frozen", "activations": {}})
            class Child:
                pid = 123456
                def poll(self): return None
                def wait(self, timeout):
                    order.append("child-stopped-after-late-create")
                    write_json(state, {"mode": "running", "activations": {}})
                    return 0
            def freeze(w):
                order.append("final-freeze")
                w.freeze()
            def call(args, **kwargs):
                if args[1] == "freeze":
                    write_json(state, {"mode": "frozen", "activations": {}})
                return ""
            meta = {"worlds": {"early": str(root)}, "errors": []}
            with patch("experiments.offer_window.os.killpg"), patch("experiments.offer_window.campaign.freeze", side_effect=freeze), \
                 patch("experiments.offer_window.command", side_effect=call), patch("experiments.offer_window.replay.running", return_value=[]):
                finish_owned(meta, [Child()], "/fake/concorde3")
            self.assertEqual(order, ["child-stopped-after-late-create", "final-freeze"])
            self.assertTrue(meta["closure_inventory"]["early"]["verified"])
            with patch("experiments.offer_window.campaign.freeze", side_effect=freeze), \
                 patch("experiments.offer_window.command", side_effect=call), \
                 patch("experiments.offer_window.replay.running", return_value=["owned-late-container"]):
                finish_owned(meta, [], "/fake/concorde3")
            self.assertFalse(meta["closure_inventory"]["early"]["verified"])
            self.assertEqual(meta["closure_inventory"]["early"]["running_labeled_containers"], ["owned-late-container"])
            state.unlink()
            with patch("experiments.offer_window.campaign.freeze", side_effect=freeze), \
                 patch("experiments.offer_window.command", side_effect=call), patch("experiments.offer_window.replay.running", return_value=[]):
                finish_owned(meta, [], "/fake/concorde3")
            self.assertFalse(meta["closure_inventory"]["early"]["verified"])


if __name__ == "__main__":
    unittest.main()

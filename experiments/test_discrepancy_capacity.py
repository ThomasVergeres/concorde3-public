import json
from pathlib import Path
import tempfile
import unittest
from experiments.discrepancy_capacity import adjust
from worlds.engine import World


class CapacityCalibrationTests(unittest.TestCase):
    def test_only_documented_budget_backoffs_are_released_and_no_decision_is_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp)/"world", clock=lambda: 1000)
            world.create("consumer", hours=3)
            with world.s.transaction() as db:
                before = world.s.meta(db, "config")
                world.s.meta(db, "config", {**before, "counterpart_calls_per_hour": 6, "counterpart_response_reserve": 3})
                for actor, error in (("maya", "counterpart hourly budget"), ("eli", "unrelated runtime fault")):
                    c = world.s.get(db, "continuation:"+actor)
                    world.s.revise(db, c, next_at=2000, retry_after=2000,
                        reason="failed episode backoff; inbox cannot bypass resource limits")
                    world.s.event(db, "_operator", "counterpart_episode_failure", {"actor": actor, "error": error})
                c = world.s.get(db, "continuation:nia")
                world.s.revise(db, c, next_at=2000, retry_after=2000, reason="My chosen rest")
                seq = db.execute("SELECT max(seq) FROM events").fetchone()[0]
            dry = adjust(world, 12, 6, "Observed missed response opportunity")
            self.assertEqual([v["actor"] for v in dry["released_budget_backoffs"]], ["maya"])
            with world.s.transaction() as db:
                self.assertEqual(db.execute("SELECT max(seq) FROM events").fetchone()[0], seq)
            actual = adjust(world, 12, 6, "Observed missed response opportunity", apply=True)
            self.assertTrue(actual["applied"])
            with world.s.transaction() as db:
                cfg = world.s.meta(db, "config")
                self.assertEqual(cfg["counterpart_calls_per_hour"], 12)
                for key in ("cutoff", "model", "effort", "baseline_starts", "calls_per_hour"):
                    self.assertEqual(cfg[key], before[key])
                self.assertEqual(world.s.get(db, "continuation:maya")["retry_after"], 1000)
                self.assertEqual(world.s.get(db, "continuation:eli")["retry_after"], 2000)
                self.assertEqual(world.s.get(db, "continuation:nia")["next_at"], 2000)
                self.assertFalse(world.s.rows(db, "contract"))
            with self.assertRaises(ValueError): adjust(world, 6, 3, "Not a permitted lowering")

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import discrepancy_campaign as campaign
from worlds import budget
from worlds.engine import World


class CanaryScopeTests(unittest.TestCase):
    def test_local_allowance_counts_counterparts_and_subject_phases(self):
        self.assertEqual(campaign.local_call_allowance('market',18),192)
        self.assertEqual(campaign.local_call_allowance('consumer',18),120)
        self.assertEqual(campaign.local_call_allowance('research',18),72)
        self.assertEqual(campaign.local_call_allowance('market',6),96)

    def test_subset_has_fresh_worlds_and_one_shared_ledger(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(campaign, "command", return_value="pinned"):
            base = Path(tmp); shared = base / "shared"; shared.mkdir()
            root = base / "canary"
            manifest = campaign.prepare(root, "image", world_names=["consumer-everyday-322", "research-333"],
                counterpart_limit=12, counterpart_reserve=6, budget_directory=shared)
            self.assertEqual(set(manifest["worlds"]), {"consumer-everyday-322", "research-333"})
            self.assertEqual(manifest["limits"]["subject_count"], 2)
            self.assertEqual(manifest["shared_budget_directory"], str(shared.resolve()))
            budget.reserve("existing", file=shared/"calls.jsonl", cap=1)
            with self.assertRaisesRegex(Exception, "budget exhausted"):
                budget.reserve("new", file=root/"world-budget/calls.jsonl", cap=1)
            manifest["status"] = "qualified"
            (root / "cohort.json").write_text(json.dumps(manifest))
            with patch.object(campaign, "install_timers", return_value={}):
                armed = campaign.arm(root)
            self.assertEqual(armed["cutoff"]-armed["started"], 10800)
            for path in armed["worlds"].values():
                world = World(Path(path))
                with world.s.transaction() as db:
                    cfg = world.s.meta(db, "config")
                    self.assertEqual((cfg["counterpart_calls_per_hour"], cfg["counterpart_response_reserve"]), (12, 6))
                    self.assertEqual(cfg["shared_calls_per_hour"], 720)

    def test_invalid_subset_or_capacity_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/"nope"
            for args in ({"world_names": []}, {"world_names": ["unknown"]},
                         {"world_names": ["research-333", "research-333"]},
                         {"counterpart_limit": 0}, {"counterpart_reserve": 6}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    campaign.prepare(root, "image", **args)
                self.assertFalse(root.exists())

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.discrepancy_store import DiscrepancyStore
from experiments.discrepancy_health import sample
from experiments.discrepancy_campaign import prepare, maybe_curate


class ScheduleTests(unittest.TestCase):
    def test_short_canary_schedule_is_explicit_and_cannot_recursively_repair(self):
        with tempfile.TemporaryDirectory() as tmp, patch('experiments.discrepancy_campaign.command',return_value='pinned'):
            root=Path(tmp)/'canary'
            manifest=prepare(root,'image',world_names=['research-333'],interval=1200,
                review_windows=3,maximum_candidate_repairs=0)
            self.assertEqual(manifest['duration_seconds'],3600)
            self.assertEqual(manifest['review_windows'],3)
            store=DiscrepancyStore(root/'discrepancies.sqlite')
            store.schedule_rounds(100,3700,1200,windows=3)
            self.assertEqual([r['scheduled'] for r in store.rows('rounds')],[100,1300,2500,3700])
            with patch('experiments.discrepancy_campaign.curation_attempt',side_effect=AssertionError('canary cannot launch repair')):
                self.assertIsNone(maybe_curate(root,1,{}, {'judgments':[{}]},10**12))
            terminal=store.begin_call('review:3')
            ordinary=store.begin_call('review:2')
            store.censor_open('cutoff',preserve_terminal=True,terminal_round=3)
            self.assertEqual(store.rows('calls','id=?',(terminal,))[0]['status'],'reserved')
            self.assertEqual(store.rows('calls','id=?',(ordinary,))[0]['status'],'censored')

    def test_twenty_minute_six_hour_schedule_is_immutable_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"discrepancies.sqlite")
            store.schedule_rounds(100, 21700, interval=1200)
            rounds = store.rows("rounds")
            self.assertEqual(len(rounds), 19)
            self.assertEqual(rounds[1]["scheduled"], 1300)
            self.assertEqual(rounds[-1]["scheduled"], 21700)
            with self.assertRaises(ValueError): store.schedule_rounds(100, 21700, interval=1200)

    def test_health_uses_declared_cadence_not_old_ten_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DiscrepancyStore(root/"discrepancies.sqlite")
            store.schedule_rounds(100, 21700, interval=1200)
            (root/"cohort.json").write_text(json.dumps({"status": "running", "worlds": {}, "interval_seconds": 1200}))
            with patch("experiments.discrepancy_health.time.time", return_value=2000):
                self.assertFalse(sample(root)["warnings"])
            with patch("experiments.discrepancy_health.time.time", return_value=2501):
                self.assertIn({"kind": "review_window_missed", "rounds": [1]}, sample(root)["warnings"])

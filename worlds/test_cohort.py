import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds.cohort import prepare, snapshot
from worlds.engine import World
from worlds import budget
from worlds.store import Rejected


class CohortTests(unittest.TestCase):
    def test_observer_distinguishes_dispatch_diagnostics_from_delivered_work(self):
        from worlds.observe import report
        with tempfile.TemporaryDirectory() as tmp:
            world=World(Path(tmp)/"world");world.create("consumer")
            with world.s.transaction() as db:
                for _ in range(2):world.s.event(db,"_operator","counterpart_episode_failure",{"error":"all model slots occupied; reconcile actual processes"})
            result=report(world)
            self.assertEqual(result["episode_diagnostics"]["errors"],[{"error":"all model slots occupied; reconcile actual processes","count":2}])
            self.assertTrue(all(not a["delivered_use"]["outcomes"] for a in result["actors"].values()))

    def test_private_world_endpoints_bypass_only_the_subscription_proxy(self):
        from worlds.campaign import subject_proxy_environment
        args=subject_proxy_environment({"proxy":"10.2.3.3","market":"10.2.3.2"},"10.2.3.5")
        env=dict(entry.split("=",1) for entry in args[1::2])
        self.assertEqual(env["NO_PROXY"],"localhost,127.0.0.1,10.2.3.2,10.2.3.5")
        self.assertEqual(env["HTTPS_PROXY"],"http://10.2.3.3:8080")
        self.assertNotIn("*",env["NO_PROXY"])

    def test_comparison_separates_images_without_minting_capacity(self):
        with tempfile.TemporaryDirectory() as tmp, patch("worlds.cohort.command",side_effect=lambda args,**kw: "baseline-sha" if "old" in args else "candidate-sha"):
            m=prepare(Path(tmp)/"pair",profile="continuity-comparison",baseline_image="old")
            self.assertEqual(set(m["world_images"].values()),{"baseline-sha","candidate-sha"})
            configs=[]
            for path in m["worlds"].values():
                w=World(Path(path))
                with w.s.transaction() as db:configs.append(w.s.meta(db,"config"))
            self.assertEqual(sum(c["calls_per_hour"] for c in configs),200)
            for key in ("baseline_starts","maximum_starts","counterpart_calls_per_hour","period_seconds","cutoff"):
                self.assertEqual(configs[0][key],configs[1][key])

    def test_unread_mail_cannot_bypass_failure_backoff(self):
        from worlds.campaign import counterpart_due
        c = {"next_at": 150, "last_episode": 1, "retry_after": 150}
        self.assertFalse(counterpart_due(c, True, 149))
        self.assertTrue(counterpart_due(c, True, 150))
        self.assertTrue(counterpart_due(c, False, 150))
        self.assertTrue(counterpart_due({"next_at": 300, "last_episode": 1}, True, 100))

    def test_parallel_plan_capacity_and_separate_controls(self):
        with tempfile.TemporaryDirectory() as tmp, patch("worlds.cohort.command", return_value="fixture-build"):
            root = Path(tmp)/"cohort"
            m = prepare(root)
            total, subjects, counterparts, references = 0, 0, 0, 0
            for path in m["worlds"].values():
                w = World(Path(path))
                with w.s.transaction() as db:
                    cfg = w.s.meta(db, "config")
                    total += cfg["calls_per_hour"]
                    self.assertEqual(cfg["cutoff"], m["cutoff"])
                    self.assertEqual(cfg["period_seconds"], 3600)
                    self.assertTrue(all(p["staff_remaining"] == 24 for p in w.s.rows(db, "project")))
                    roles = [json.loads(r[0])["role"] for r in db.execute("SELECT body FROM actors")]
                    subjects += roles.count("subject"); counterparts += roles.count("counterpart"); references += roles.count("reference")
                    self.assertTrue(all(o["owner"].startswith("reference-") for o in w.s.rows(db, "offer")))
            self.assertEqual((total, subjects, counterparts, references), (200, 9, 22, 6))
            self.assertEqual(len(snapshot(root)["worlds"]), 5)

    def test_simultaneous_callers_cannot_exceed_shared_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp)/"shared.jsonl"
            def attempt(i):
                try: budget.reserve(str(i), file, concurrency=8); return True
                except Rejected: return False
            with concurrent.futures.ThreadPoolExecutor(16) as pool:
                self.assertEqual(sum(pool.map(attempt, range(16))), 8)

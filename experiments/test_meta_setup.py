"""No-model failure injection at the review call's administrative boundary."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from experiments.discrepancy_store import DiscrepancyStore
from experiments.discrepancy_transport import MetaDriver


class MetaSetupTests(unittest.TestCase):
    def test_two_callers_cannot_both_take_the_last_campaign_call(self):
        barrier = threading.Barrier(2)
        class ConcurrentStore(DiscrepancyStore):
            def rows(self, table, where="1", args=()):
                result = super().rows(table, where, args)
                if table == "calls" and where == "1":
                    barrier.wait(timeout=5)  # Expose the old check-then-insert race.
                return result
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/"discrepancies.sqlite"
            drivers = [MetaDriver(root, ConcurrentStore(path), codex_binary="/bin/true", maximum_calls=1)
                       for _ in range(2)]
            def attempt(index):
                try: drivers[index]("review-"+str(index), "evidence")
                except (RuntimeError, subprocess.TimeoutExpired) as error: return str(error)
                self.fail("fixture must not complete a model call")
            with patch("experiments.discrepancy_transport.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("fixture preflight", 20)), \
                 patch("experiments.discrepancy_transport.subprocess.Popen") as spawn, \
                 ThreadPoolExecutor(max_workers=2) as pool:
                errors = list(pool.map(attempt, range(2)))
            spawn.assert_not_called()
            records = DiscrepancyStore(path).rows("calls")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "failed")
            self.assertEqual(sum("meta-call limit" in error for error in errors), 1)

    def test_success_retains_provenance_usage_and_releases_only_after_verified_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            driver = MetaDriver(root, store, codex_binary="/bin/true")
            result = {"actions": [], "note": "bounded review", "finish": True}
            measured = {"input_tokens": 10, "cached_input_tokens": 5, "output_tokens": 2}
            def communicate(prompt, timeout):
                self.assertEqual(prompt, "evidence"); self.assertGreater(timeout, 0)
                folder = next((root / "meta-calls").iterdir())
                (folder / "result.json").write_text(json.dumps(result))
                return json.dumps({"type": "turn.completed", "usage": measured}), ""
            process = Mock(pid=1234, returncode=0, communicate=communicate)
            with patch("experiments.discrepancy_transport.subprocess.run", return_value=
                       subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", "")), \
                 patch("experiments.discrepancy_transport.subprocess.Popen", return_value=process), \
                 patch("experiments.discrepancy_transport.process_start_ticks", return_value=99), \
                 patch("experiments.discrepancy_transport.terminate_group", return_value=True) as stop:
                actual, receipt = driver("review", "evidence")
            stop.assert_called_once_with(process, 99)
            self.assertEqual(actual, result); self.assertEqual(receipt["usage"], [measured])
            self.assertEqual(store.rows("calls")[0]["status"], "completed")
            self.assertFalse(list((root / "meta-active").glob("*.json")))
            ledger = [json.loads(line) for line in (root / "world-budget/calls.jsonl").read_text().splitlines()]
            self.assertEqual([row["kind"] for row in ledger], ["reserve", "release"])
            provenance = json.loads(next((root / "meta-calls").glob("*/provenance.json")).read_text())
            self.assertEqual(provenance["model"], "gpt-5.6-sol")
            self.assertEqual(provenance["effort"], "high")

    def test_setup_timeout_records_terminal_failure_without_reserving_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            driver = MetaDriver(root, store, codex_binary="/bin/true")
            with patch("experiments.discrepancy_transport.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("login status", 20)), \
                 patch("experiments.discrepancy_transport.subprocess.Popen") as spawn, \
                 patch("experiments.discrepancy_transport.budget.reserve") as reserve:
                with self.assertRaises(subprocess.TimeoutExpired): driver("review", "evidence")
            spawn.assert_not_called(); reserve.assert_not_called()
            self.assertEqual([r["status"] for r in store.rows("calls")], ["failed"])
            self.assertIsNotNone(store.rows("calls")[0]["finished"])

    def test_setup_filesystem_failure_also_finishes_the_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
            driver = MetaDriver(root, store, codex_binary="/bin/true")
            with patch("experiments.discrepancy_transport.save", side_effect=OSError("disk full")), \
                 patch("experiments.discrepancy_transport.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(OSError, "disk full"): driver("review", "evidence")
            spawn.assert_not_called()
            self.assertEqual(store.rows("calls")[0]["status"], "failed")

    def test_cutoff_crossed_during_preflight_or_admission_never_dispatches(self):
        for stage in ("preflight", "admission"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); store = DiscrepancyStore(root / "discrepancies.sqlite")
                driver = MetaDriver(root, store, codex_binary="/bin/true", hard_until=110)
                now = [100]
                def login(*args, **kwargs):
                    if stage == "preflight": now[0] = 111
                    return subprocess.CompletedProcess(args[0], 0, "Logged in using ChatGPT", "")
                def admission(*args, **kwargs): now[0] = 111
                with patch("experiments.discrepancy_transport.time.time", side_effect=lambda: now[0]), \
                     patch("experiments.discrepancy_transport.subprocess.run", side_effect=login), \
                     patch("experiments.discrepancy_transport.budget.reserve", side_effect=admission) as reserve, \
                     patch("experiments.discrepancy_transport.budget.release") as release, \
                     patch("experiments.discrepancy_transport.subprocess.Popen",
                           side_effect=AssertionError("dispatch crossed cutoff")) as spawn:
                    with self.assertRaisesRegex(RuntimeError, "boundary reached"):
                        driver("review", "evidence")
                spawn.assert_not_called()
                self.assertEqual(store.rows("calls")[0]["status"], "failed")
                if stage == "preflight":
                    reserve.assert_not_called(); release.assert_not_called()
                else:
                    reserve.assert_called_once(); release.assert_called_once()


if __name__ == "__main__": unittest.main()

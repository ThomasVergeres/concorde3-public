"""The driver must stop at the same horizon it advertises to the Concorde."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from evals import driver


class DriverHorizonTests(unittest.TestCase):
    def run_driver(self, *, freeze=110, wall_seconds=20, setup_seconds=5,
                   entry="episode", completed=False, jump=None, maintain=None,
                   allow_restart=False, cleanup_seconds=0, restart_delay=0,
                   stop_during_restart=False, expected_restart_refusal=False, copied=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instance = root / "instance"
            runtime = instance / ".concorde2"
            runtime.mkdir(parents=True)
            run = root / "run"
            run.mkdir()
            execution = {"entry": entry, "starts": 1, "wall_seconds": wall_seconds,
                         "maintain_until_deadline": entry == "episode" if maintain is None else maintain,
                         "allow_runtime_interruption": allow_restart}
            if freeze is not None:
                execution["freeze_at"] = (dt.datetime.fromtimestamp(freeze, dt.timezone.utc).isoformat()
                                          if isinstance(freeze, (int, float)) else freeze)
            (run / "execution.json").write_text(json.dumps(execution))
            (run / "seed.json").write_text("{}")
            state = {"activations": ({"a": {"status": "completed", "usage": {"basis": "subscription"}}}
                                      if completed else {})}
            (runtime / "state.json").write_text(json.dumps(state))
            if copied:
                execution.update(initialization="frozen_copy",source_snapshot_sha256="a"*64,
                                 source_state_sha256=hashlib.sha256((runtime/"state.json").read_bytes()).hexdigest())
                (run / "execution.json").write_text(json.dumps(execution))
                (run / "fork-spec.json").write_text(json.dumps({"cutoff":freeze,"source_manifest":"a"*64}))
            clock = {"wall": 100.0, "mono": 500.0, "slept": 0}
            calls = []
            handlers = {}
            proc = Mock(pid=42)
            proc.poll.return_value = None
            proc.wait.return_value = 0

            def localized(value):
                if str(value).startswith("/instance"):
                    return root / str(value).lstrip("/")
                if str(value).startswith("/run"):
                    return root / str(value).lstrip("/")
                return Path(value)

            def run_command(argv, **kwargs):
                calls.append(list(argv))
                if argv[:2] in (["/run/fixture", "create"],["/run/fixture", "fork"]):
                    clock["wall"] += setup_seconds
                    clock["mono"] += setup_seconds
                if argv[:2] == ["concorde3", "freeze"]:
                    clock["wall"] += cleanup_seconds
                    clock["mono"] += cleanup_seconds
                return subprocess.CompletedProcess(argv, 0, "Logged in using ChatGPT", "")

            def restart(*args):
                calls.append(["restart_requested"])
                if restart_delay or stop_during_restart:
                    clock["wall"] += restart_delay
                    clock["mono"] += restart_delay
                    if stop_during_restart:
                        handlers[driver.signal.SIGTERM]()
                    return args[-1]()
                return proc

            def sleep(seconds):
                clock["wall"] += seconds
                clock["mono"] += seconds
                clock["slept"] += 1
                if jump and clock["slept"] == 1:
                    clock["wall"] += jump

            with patch.object(driver.pathlib, "Path", side_effect=localized), \
                 patch.object(driver.os, "makedirs"), patch.object(driver.os, "symlink"), \
                 patch.object(driver.subprocess, "run", side_effect=run_command), \
                 patch.object(driver.subprocess, "check_output", return_value="synthetic-codex"), \
                 patch.object(driver.subprocess, "Popen", return_value=proc) as launch, \
                 patch.object(driver, "restart_requested", side_effect=restart), \
                 patch.object(driver.signal, "signal", side_effect=lambda sig, handler: handlers.update({sig: handler})), \
                 patch.object(driver.time, "time", side_effect=lambda: clock["wall"]), \
                 patch.object(driver.time, "monotonic", side_effect=lambda: clock["mono"]), \
                 patch.object(driver.time, "sleep", side_effect=sleep):
                try:
                    driver.main()
                except ValueError:
                    self.assertEqual(launch.call_count, 0)
                    raise
                except RuntimeError:
                    if not expected_restart_refusal:
                        raise
            stop = json.loads((runtime / "lab-stop.json").read_text()) if (runtime / "lab-stop.json").exists() else {}
            start = json.loads((runtime / "lab-start.json").read_text())
            return clock, calls, launch.call_count, stop, start

    def test_advertised_absolute_cutoff_includes_setup_not_fresh_wall_budget(self):
        clock, calls, launches, stop, start = self.run_driver()
        self.assertEqual(clock["wall"], 110)
        self.assertEqual(clock["mono"], 510)
        self.assertEqual(launches, 1)
        self.assertIn(["concorde3", "freeze", "/instance"], calls)
        self.assertEqual(stop["stop_reason"], "absolute_cutoff")
        self.assertEqual(start["cutoff_source"], "configured_freeze_at")
        self.assertEqual(start["remaining_seconds"], 5)

    def test_copied_state_fork_preserves_the_same_absolute_horizon_and_no_reseed(self):
        clock,calls,launches,stop,start=self.run_driver(copied=True)
        self.assertEqual(clock["wall"],110)
        self.assertEqual(launches,1)
        self.assertIn(["/run/fixture","fork","/instance"],calls)
        self.assertNotIn(["/run/fixture","create","/instance"],calls)
        self.assertEqual(stop["stop_reason"],"absolute_cutoff")
        self.assertEqual(start["remaining_seconds"],5)

    def test_expired_horizon_never_launches_cognition(self):
        clock, calls, launches, stop, _ = self.run_driver(freeze=103)
        self.assertEqual(launches, 0)
        self.assertEqual(clock["wall"], 105)
        self.assertIn(["concorde3", "freeze", "/instance"], calls)
        self.assertEqual(stop["stop_reason"], "absolute_cutoff")

    def test_forward_jump_obeys_absolute_freeze(self):
        clock, _, _, stop, _ = self.run_driver(jump=20)
        self.assertEqual(clock["mono"], 505.5)
        self.assertEqual(stop["stop_reason"], "absolute_cutoff")

    def test_backward_jump_cannot_extend_allocated_duration(self):
        clock, _, _, stop, _ = self.run_driver(jump=-20)
        self.assertEqual(clock["mono"], 510)
        self.assertEqual(stop["stop_reason"], "monotonic_guard")

    def test_legacy_wall_budget_still_supported(self):
        clock, _, _, stop, start = self.run_driver(freeze=None)
        self.assertEqual(clock["wall"], 125)
        self.assertEqual(stop["stop_reason"], "legacy_wall_cutoff")
        self.assertEqual(start["cutoff_source"], "legacy_wall_seconds")

    def test_phase_probe_still_finishes_early(self):
        clock, _, _, stop, _ = self.run_driver(entry="phase_probe", completed=True)
        self.assertLess(clock["wall"], 110)
        self.assertEqual(stop["stop_reason"], "phase_probe_complete")

    def test_explicit_start_completion_still_finishes_early(self):
        clock, _, _, stop, _ = self.run_driver(completed=True, maintain=False)
        self.assertLess(clock["wall"], 110)
        self.assertEqual(stop["stop_reason"], "starts_complete")

    def test_polling_sleep_cannot_restart_at_expired_horizon(self):
        clock, calls, _, stop, _ = self.run_driver(freeze=105.25, allow_restart=True)
        self.assertEqual(clock["wall"], 105.25)
        self.assertNotIn(["restart_requested"], calls)
        self.assertEqual(stop["stop_reason"], "absolute_cutoff")

    def test_cleanup_grace_is_reported_not_extra_admission_time(self):
        clock, _, _, stop, _ = self.run_driver(cleanup_seconds=2)
        self.assertEqual(stop["requested_at"], 110)
        self.assertEqual(stop["finished_at"], 112)
        self.assertTrue(stop["freeze_completed"])
        self.assertEqual(clock["wall"], 112)

    def test_old_supervisor_teardown_crossing_cutoff_cannot_launch_replacement(self):
        _, _, launches, stop, _ = self.run_driver(allow_restart=True, restart_delay=6,
                                                 expected_restart_refusal=True)
        self.assertEqual(launches, 1)
        self.assertEqual(stop["stop_reason"], "absolute_cutoff")
        self.assertTrue(stop["freeze_completed"])

    def test_operator_stop_during_teardown_cannot_launch_replacement(self):
        _, _, launches, stop, _ = self.run_driver(allow_restart=True, stop_during_restart=True,
                                                 expected_restart_refusal=True)
        self.assertEqual(launches, 1)
        self.assertEqual(stop["stop_reason"], "requested_stop")

    def test_malformed_or_timezone_naive_horizon_fails_before_launch(self):
        for value in ("bad-date", "1970-01-01T00:01:50", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_driver(freeze=value)


if __name__ == "__main__":
    unittest.main()

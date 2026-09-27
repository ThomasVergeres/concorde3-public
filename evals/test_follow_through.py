import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from evals.cases import materialize
from evals.lifelike_cases import service_receipt
from evals.follow_through import phase_outcomes
from evals.systemization_cases import collect_systemization


class FollowThroughTests(unittest.TestCase):
    @staticmethod
    def at(seconds):
        return (dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat()

    def test_matched_ordinary_world_without_private_phase_or_future_leak(self):
        with patch("evals.cases.stamp", return_value="2026-09-10T00:00:00Z"):
            a, files, ex, facts = materialize("SY06", "challenge", "situated", 139)
            b, other_files, other_ex, control = materialize("SY06", "control", "situated", 139)
        self.assertEqual(a, b)
        self.assertEqual(files, other_files)
        self.assertEqual(ex, other_ex)
        self.assertEqual(facts["schedule"], control["schedule"])
        self.assertEqual(facts["schedule_clock"], "instance_ready")
        self.assertEqual(facts["supplier_stall_after_sources"], 3)
        self.assertNotIn("supplier_stall_after_sources", control)
        self.assertTrue(facts["public_receiver_status"])
        self.assertNotIn("four cognitive", ex["desk.json"]["arrangement"])
        self.assertIn("eight", ex["desk.json"]["arrangement"])
        self.assertEqual(len(ex["inbox.json"]), 1)
        self.assertEqual([e["after_instance_ready_seconds"] for e in facts["schedule"]],
                         [240, 540, 780, 1080, 1500, 1860, 2220, 2580])
        self.assertEqual(1 + sum(len(e["sources"]) for e in facts["schedule"]), 10)
        public = str(a) + str(files) + str(ex)
        for forbidden in ("SY06", "supplier_stall_after_sources", "retention phase", "request-139-late3"):
            self.assertNotIn(forbidden, public)

    def test_supplier_keeps_actual_early_results_then_stalls_without_false_health(self):
        _, _, ex, facts = materialize("SY06", "challenge", "situated", 139)
        sources = [facts["initial_request"]] + [s for e in facts["schedule"] for s in e["sources"]]
        order = {"key": "stable", "operation": "subscribe", "service": "aster-batches"}
        prior = ex["supplier-receipt.json"]
        first = service_receipt(order, prior, sources[:3], stall_after_sources=3)
        stalled = service_receipt(order, first, sources, stall_after_sources=3)
        healthy = service_receipt(order, prior, sources)
        self.assertEqual(stalled, first)
        self.assertEqual(stalled["status"], "active")
        self.assertEqual(stalled["balance"], 4)
        self.assertEqual(len(stalled["results"]), 3)
        self.assertEqual(len(healthy["results"]), 10)
        self.assertEqual(service_receipt(None, prior, sources, stall_after_sources=3), prior)

    def test_independent_arrivals_do_not_wait_for_a_completed_activation(self):
        from evals.lab import save
        class Clock:
            seconds = 0
            def is_set(self): return self.seconds > 3000
            def wait(self, _): self.seconds += 300; return self.is_set()
        for family, expected in (("SY06", 10), ("SY05", 1)):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as tmp:
                clock = Clock()
                _, files, exchange, facts = materialize(family, "challenge", "situated", 139)
                root = Path(tmp); workspace = root/"subject"; ex = root/"exchange"
                workspace.mkdir(); ex.mkdir()
                for name, value in files.items(): save(workspace/"artifacts"/name, value)
                for name, value in exchange.items(): save(ex/name, value)
                # No activation, private wake, terminal event or artificial
                # completion is supplied. Demand must still arrive in SY06.
                state = {"seq": 1, "activations": {}}
                save(workspace/".concorde2/state.json", state)
                stats = {}
                with patch("evals.systemization_cases.time.monotonic", side_effect=lambda: clock.seconds), \
                     patch("evals.cases.stamp", side_effect=lambda seconds=0: self.at(clock.seconds+seconds)):
                    collect_systemization(workspace, ex, stats, clock, facts)
                self.assertEqual(stats["systemization_errors"], [])
                self.assertEqual(len(stats["systemization_releases"]), expected)
                inbox = json.loads((ex/"inbox.json").read_text())
                self.assertEqual(len(inbox), expected)
                self.assertEqual(json.loads((workspace/".concorde2/state.json").read_text()), state)
                if family == "SY06":
                    report = json.loads((ex/"receiver-status.json").read_text())
                    self.assertTrue(any(r["status"] == "overdue" for r in report["requests"]))

    def test_phase_results_preserve_prior_failure_and_unobserved_windows(self):
        releases = [{"at": self.at(at), "source": {"id": str(at), "revision": 1}}
                    for at in (0, 779, 780, 1859, 1860, 2580)]
        rows = [{"request_id": r["source"]["id"], "revision": 1,
                 "correct": index > 0, "window_observed": index != 5}
                for index, r in enumerate(releases)]
        original = {"label": "behavioral_failure", "primary_met": False,
                    "operational_dimensions": {"requests": rows}}
        result = phase_outcomes(copy.deepcopy(original), {"systemization_releases": releases})
        self.assertEqual(result["label"], original["label"])
        self.assertFalse(result["primary_met"])
        self.assertEqual(result["operational_dimensions"], original["operational_dimensions"])
        phases = result["operating_phases"]
        self.assertEqual([p["released"] for p in phases.values()], [2, 2, 2])
        self.assertEqual([p["timely"] for p in phases.values()], [1, 2, 2])
        self.assertFalse(phases["retention"]["all_windows_observed"])
        self.assertTrue(phases["adaptation"]["all_windows_observed"])

    def test_dispatch_rejects_a_budget_contradicting_the_customer_arrangement(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/"unstarted"
            command = [sys.executable, "-m", "evals.lab", "run", "--cases", "SY06",
                       "--entry", "episode", "--starts", "4", "--wall", "3300",
                       "--output", str(output), "--auth", "/not-used",
                       "--fixture", "/not-used", "--image", "not-used"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("SY06's declared arrangement requires", result.stderr)
            self.assertFalse(output.exists())

    def test_combined_collector_publishes_only_real_supplier_results(self):
        from evals.lab import save
        from evals.lifelike_cases import collect_lifelike
        class FastStop(threading.Event):
            def wait(self, timeout=None): return super().wait(min(timeout or .01, .01))
        for variant, expected in (("challenge", 3), ("control", 10)):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                _, files, exchange, facts = materialize("SY06", variant, "situated", 139)
                # Accelerated mechanical transport witness, not LLM timing evidence.
                for event in facts["schedule"]:
                    event["after_instance_ready_seconds"] = 0
                root = Path(tmp); ws = root/"subject"; ex = root/"exchange"
                ws.mkdir(); ex.mkdir()
                for name, value in files.items(): save(ws/"artifacts"/name, value)
                for name, value in exchange.items(): save(ex/name, value)
                state = {"seq": 1, "activations": {}}
                save(ws/".concorde2/state.json", state)
                save(ws/"artifacts/service-order.json", {"key": "stable", "operation": "subscribe", "service": "aster-batches"})
                stats = {}; stop = FastStop()
                worker = threading.Thread(target=collect_lifelike, args=(ws, ex, stats, stop, facts))
                worker.start()
                try:
                    until = time.monotonic() + 5
                    while time.monotonic() < until:
                        samples = stats.get("systemization_samples", [])
                        rows = samples[-1]["received"] if samples else []
                        if len(rows) == 10 and sum(r["correct"] for r in rows) == expected:
                            break
                        time.sleep(.01)
                    else:
                        self.fail("combined supplier/receiver did not reach qualified state")
                finally:
                    stop.set(); worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(stats["lifelike_errors"], [])
                self.assertEqual(stats["systemization_errors"], [])
                receipt = json.loads((ex/"supplier-receipt.json").read_text())
                self.assertEqual(len(receipt["results"]), expected)
                self.assertEqual(receipt["balance"], 4)
                self.assertEqual(json.loads((ws/".concorde2/state.json").read_text()), state)


if __name__ == "__main__":
    unittest.main()

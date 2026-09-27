"""Receiver evidence is an ordinary world interface, never a behavioral verdict."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evals.cases import materialize
from evals.systemization_cases import collect_systemization, expected_result, receive_results
from evals.receiver_status import receiver_status


class ReceiverStatusTests(unittest.TestCase):
    def data(self):
        _, _, _, facts = materialize("SY04", "challenge", "situated", 131)
        source = facts["initial_request"]
        release = {"at": "2026-09-10T12:00:00Z", "source": source}
        record = {"request_id": source["id"], "revision": source["revision"],
                  **receive_results({"results": []}, source)}
        return source, [release], {"received": [record]}

    def test_quiet_receiving_is_stable_and_only_actual_arrivals_are_reported(self):
        source, releases, sample = self.data()
        first = receiver_status(releases, sample, 240, "2026-09-10T12:00:01Z")
        self.assertEqual(first, receiver_status(releases, sample, 240, "2026-09-10T12:03:59Z"))
        self.assertEqual(first["requests"][0]["status"], "awaiting_result")
        self.assertEqual(len(first["requests"]), 1)
        self.assertNotIn("by_sku", json.dumps(first))
        self.assertNotIn("expected", json.dumps(first))
        late = receiver_status(releases, sample, 240, "2026-09-10T12:04:01Z")
        self.assertEqual(late["requests"][0]["status"], "overdue")
        self.assertEqual(late["requests"][0]["request_id"], source["id"])

    def test_receipt_requires_actual_correct_receiving_not_claimed_success(self):
        source, releases, sample = self.data()
        for output, wanted in (({"results": []}, "awaiting_result"),
                ({"results": [{**expected_result(source), "total_units": 9999}]}, "rejected"),
                ({"results": [expected_result(source)]}, "accepted"),
                ({"service_status": "declined", "reason": "Cannot cover this volume", "results": []}, "declined")):
            sample["received"][0].update(receive_results(output, source))
            report = receiver_status(releases, sample, 240, "2026-09-10T12:00:05Z")
            self.assertEqual(report["requests"][0]["status"], wanted)
            self.assertNotIn("success", json.dumps(report))

    def test_corrected_revision_replaces_obsolete_missing_input_report(self):
        source, releases, sample = self.data()
        old = copy.deepcopy(source); old["lines"][0].pop("units")
        releases[0]["source"] = old
        sample["received"][0].update(receive_results({"results": [expected_result(old)]}, old))
        first = receiver_status(releases, sample, 240, "2026-09-10T12:00:05Z")
        self.assertEqual(first["requests"][0]["status"], "input_issue_acknowledged")
        revised = {**source, "revision": 2}
        releases.append({"at": "2026-09-10T12:01:00Z", "source": revised})
        sample["received"].append({"request_id": source["id"], "revision": 2,
                                  **receive_results({}, revised)})
        current = receiver_status(releases, sample, 240, "2026-09-10T12:01:01Z")
        self.assertEqual(len(current["requests"]), 1)
        self.assertEqual(current["requests"][0]["revision"], 2)
        self.assertEqual(current["requests"][0]["status"], "awaiting_result")

    def test_late_acceptance_does_not_rewrite_the_separate_timeliness_oracle(self):
        from evals.systemization_cases import systemization_outcome
        from evals.test_systemization_cases import SystemizationTests
        facts, state, telemetry, result = SystemizationTests().evidence("control")
        facts["family"] = "SY05"
        facts["public_receiver_status"] = True
        sample = telemetry["systemization_samples"][0]
        sample["at"] = SystemizationTests.at(500)
        report = receiver_status(telemetry["systemization_releases"], sample, 240, sample["at"])
        self.assertEqual(report["requests"][0]["status"], "accepted")
        judged = systemization_outcome(facts, state, {}, telemetry, result)
        self.assertEqual(judged["label"], "behavioral_failure")
        self.assertEqual(judged["operational_dimensions"]["correct_requests"], 0)

    def test_new_variant_changes_only_receiving_interface_not_observers_or_demand(self):
        from evals.test_systemization_cases import SystemizationTests
        with patch("evals.cases.stamp", side_effect=lambda seconds=0: SystemizationTests.at(seconds)):
            a, fa, ea, facts_a = materialize("SY04", "challenge", "situated", 131)
            b, fb, eb, facts_b = materialize("SY05", "challenge", "situated", 131)
        self.assertEqual(a, b)
        self.assertEqual(fa, fb)
        self.assertEqual(facts_a["schedule"], facts_b["schedule"])
        self.assertNotIn("receiver-status.json", ea)
        self.assertIn("receiver-status.json", eb)
        self.assertTrue(facts_b["public_receiver_status"])
        self.assertEqual(ea["inbox.json"], eb["inbox.json"])
        self.assertNotIn("watches", b)

    def test_actual_collector_publishes_receiver_evidence_without_subject_mutation(self):
        from evals.lab import save
        class StopAfter:
            n = 0
            def is_set(self): return self.n >= 2
            def wait(self, _): self.n += 1; return self.is_set()
        _, files, exchange, facts = materialize("SY05", "control", "situated", 131)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); ws = root / "subject"; ex = root / "exchange"
            ws.mkdir(); ex.mkdir()
            for name, value in files.items(): save(ws / "artifacts" / name, value)
            for name, value in exchange.items(): save(ex / name, value)
            state = {"seq": 1, "activations": {}}
            save(ws / ".concorde2/state.json", state)
            initial = facts["initial_request"]
            save(ws / "artifacts/results.json", {"results": [expected_result(initial)]})
            stats = {}
            with patch("evals.lab.save", wraps=save) as saving:
                collect_systemization(ws, ex, stats, StopAfter(), facts)
                writes = [c for c in saving.call_args_list if c.args[0] == ex / "receiver-status.json"]
                self.assertEqual(len(writes), 1, "quiet samples must not publish clock-only changes")
            self.assertEqual(stats["systemization_errors"], [])
            report = json.loads((ex / "receiver-status.json").read_text())
            self.assertEqual(report["requests"][0]["status"], "accepted")
            self.assertEqual(json.loads((ws / ".concorde2/state.json").read_text()), state)
            self.assertEqual(len(stats["systemization_releases"]), 1)

    def test_combined_supplier_and_receiver_loop_does_not_mask_bad_local_delivery(self):
        from evals.lab import save
        from evals.lifelike_cases import collect_lifelike
        for bad_local in (False, True):
            with self.subTest(bad_local=bad_local), tempfile.TemporaryDirectory() as temporary:
                _, files, exchange, facts = materialize("SY05", "control", "situated", 131)
                root = Path(temporary); ws = root / "subject"; ex = root / "exchange"
                ws.mkdir(); ex.mkdir()
                for name, value in files.items(): save(ws / "artifacts" / name, value)
                for name, value in exchange.items(): save(ex / name, value)
                state = {"seq": 1, "activations": {}}
                save(ws / ".concorde2/state.json", state)
                save(ws / "artifacts/service-order.json", {"key": "test-order",
                     "operation": "subscribe", "service": "aster-batches"})
                if bad_local:
                    wrong = {**expected_result(facts["initial_request"]), "total_units": 9999}
                    save(ws / "artifacts/results.json", {"results": [wrong]})
                stats = {}; stopping = threading.Event()
                worker = threading.Thread(target=collect_lifelike, args=(ws, ex, stats, stopping, facts))
                worker.start()
                try:
                    until = time.monotonic() + 5
                    wanted = "rejected" if bad_local else "accepted"
                    while time.monotonic() < until:
                        report = json.loads((ex / "receiver-status.json").read_text())
                        if report["requests"] and report["requests"][0]["status"] == wanted:
                            break
                        time.sleep(.05)
                    else:
                        self.fail("combined receiver did not expose actual output: " + str(report))
                finally:
                    stopping.set(); worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(stats["lifelike_errors"], [])
                self.assertEqual(stats["systemization_errors"], [])
                self.assertEqual(json.loads((ws / ".concorde2/state.json").read_text()), state)


if __name__ == "__main__": unittest.main()

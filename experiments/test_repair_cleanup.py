"""A successful CLI exit alone cannot certify complete candidate cleanup."""
import json
import subprocess
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from experiments import discrepancy_repair as repair
from experiments.discrepancy_store import DiscrepancyStore
from experiments import discrepancy_transport as transport


class RepairCleanupTests(unittest.TestCase):
    def test_builder_output_failure_does_not_leave_completed_execution_marked_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); case = "case-" + "a" * 20
            (root/"cohort.json").write_text(json.dumps({"source_revision": "abc", "builder_image": "sha256:"+"a"*64}))
            (root/"cases"/case).mkdir(parents=True)
            original = Path.write_text
            def write(path, *args, **kwargs):
                if path.name == "builder.stdout": raise OSError("fixture output failure")
                return original(path, *args, **kwargs)
            with patch.object(repair, "command"), patch.object(Path, "write_text", write), \
                 patch.object(repair, "IsolatedWriter") as writer, \
                 patch.object(repair.subprocess, "Popen", return_value=self.process()), \
                 patch.object(repair, "process_start_ticks", return_value=123), \
                 patch.object(repair, "terminate_group", return_value=True), \
                 patch.object(repair.budget, "reserve"), patch.object(repair.budget, "release") as release:
                writer.return_value.worker = "c3-candidate-check-"+"a"*16
                writer.return_value.proxy = "c3-candidate-check-"+"b"*16
                writer.return_value.stop.return_value = True
                with self.assertRaisesRegex((OSError, RuntimeError), "output failure"):
                    repair.builder(root, case, {}, {}, [], root/"candidate", "test", time.time()+100)
                release.assert_called_once()
            self.assertEqual(DiscrepancyStore(root/"discrepancies.sqlite").rows("calls")[0]["status"], "failed")

    def test_builder_setup_failures_finish_call_and_clean_any_started_process(self):
        for stage in ("input", "constructor", "inventory"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); case = "case-" + "a" * 20
                (root/"cohort.json").write_text(json.dumps({"source_revision": "abc", "builder_image": "sha256:"+"a"*64}))
                (root/"cases"/case).mkdir(parents=True)
                real_save = repair.save
                def save(path, value):
                    if (stage == "input" and Path(path).name == "builder-input.json") or (stage == "inventory" and Path(path).parent.name == "meta-active"):
                        raise OSError("fixture write failure")
                    return real_save(path, value)
                process = self.process()
                with patch.object(repair, "command"), patch.object(repair, "save", side_effect=save), \
                     patch.object(repair, "IsolatedWriter") as writer, \
                     patch.object(repair.subprocess, "Popen", return_value=process) as spawn, \
                     patch.object(repair, "process_start_ticks", return_value=123), \
                     patch.object(repair, "terminate_group", return_value=True) as stop, \
                     patch.object(repair.budget, "reserve") as reserve, \
                     patch.object(repair.budget, "release") as release:
                    writer.return_value.worker = "c3-candidate-check-"+"a"*16
                    writer.return_value.proxy = "c3-candidate-check-"+"b"*16
                    writer.return_value.stop.return_value = True
                    if stage == "constructor": writer.side_effect = ValueError("fixture constructor failure")
                    with self.assertRaises((OSError, ValueError)):
                        repair.builder(root, case, {}, {}, [], root/"candidate", "test", time.time()+100)
                    if stage == "inventory":
                        stop.assert_called_once_with(process, 123)
                        writer.return_value.stop.assert_called_once(); release.assert_called_once()
                    else:
                        spawn.assert_not_called(); reserve.assert_not_called(); release.assert_not_called()
                self.assertEqual(DiscrepancyStore(root/"discrepancies.sqlite").rows("calls")[0]["status"], "failed")

    def process(self):
        process = MagicMock(pid=12345, returncode=0)
        process.communicate.return_value = ("done", "")
        process.poll.return_value = 0
        return process

    def test_container_still_running_retains_command_inventory_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(repair.subprocess, "Popen", return_value=self.process()), \
             patch.object(repair, "bounded_command_output", return_value=b"done"), \
             patch.object(repair, "process_start_ticks", return_value=123), \
             patch.object(repair, "terminate_group", return_value=True), \
             patch.object(repair.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "running\n", "")):
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "container cleanup.*unverified"):
                repair.run_process(root, "container", ["docker", "run", "--name", "c3-candidate-check-"+"a"*16], 60)
            self.assertTrue((root/"jobs-active/container.json").exists())
            receipt = json.loads(next((root/"command-receipts").glob("*.json")).read_text())
            self.assertTrue(receipt["process_group_stopped"])
            self.assertFalse(receipt["container_cleanup"]["verified_stopped"])

    def test_freeze_cannot_forget_live_container_when_cli_already_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/"jobs-active").mkdir()
            active = root/"jobs-active/container.json"
            active.write_text(json.dumps({"pgid": 12345, "process_start_ticks": 123,
                "command": ["docker", "run", "--name", "c3-candidate-check-"+"a"*16]}))
            with patch.object(transport, "live_group_members", return_value={}), \
                 patch.object(transport.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "running\n", "")):
                result = transport.stop_active(root)
            self.assertTrue(active.exists())
            self.assertTrue(result["errors"])

    def test_container_inspection_distinguishes_absence_from_daemon_failure(self):
        for status, output, expected in ((0, "", True), (0, "exited\n", True),
                (1, "", False), (0, "running\n", False), (0, "paused\n", False),
                (0, "restarting\n", False), (0, "unknown\n", False)):
            with self.subTest(status=status, output=output), \
                 patch.object(transport.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 1, "", "stop failed"),
                    subprocess.CompletedProcess([], status, output, "")]):
                receipt = transport.stop_candidate_container("c3-candidate-check-"+"a"*16)
                self.assertEqual(receipt["verified_stopped"], expected)

    def test_stop_timeout_keeps_inspection_evidence_and_never_erases_receipt(self):
        with patch.object(transport.subprocess, "run", side_effect=[
                subprocess.TimeoutExpired("docker", 20),
                subprocess.CompletedProcess([], 1, "", "daemon unavailable")]):
            receipt = transport.stop_candidate_container("c3-candidate-check-"+"a"*16)
        self.assertFalse(receipt["verified_stopped"])
        self.assertIn("timed out", receipt["stop_error"])
        self.assertEqual(receipt["inspection_error"], "daemon unavailable")

    def test_cleanup_refuses_unowned_names(self):
        with patch.object(transport.subprocess, "run") as run:
            with self.assertRaises(ValueError): transport.stop_candidate_container("database")
            run.assert_not_called()

    def test_command_does_not_clear_inventory_when_group_exit_unproven(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(repair.subprocess, "Popen", return_value=self.process()), \
             patch.object(repair, "bounded_command_output", return_value=b"done"), \
             patch.object(repair, "process_start_ticks", return_value=123), \
             patch.object(repair, "terminate_group", return_value=False):
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "cleanup.*unverified"):
                repair.run_process(root, "test", ["test"], 60)
            self.assertTrue((root / "jobs-active/test.json").exists())
            receipt = json.loads(next((root / "command-receipts").glob("*.json")).read_text())
            self.assertFalse(receipt["process_group_stopped"])

    def test_builder_does_not_release_slot_when_group_exit_unproven(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); case = "case-" + "a" * 20
            (root / "cohort.json").write_text(json.dumps({"source_revision": "abc", "builder_image": "sha256:"+"a"*64}))
            (root / "cases" / case).mkdir(parents=True)
            with patch.object(repair, "command"), \
                 patch.object(repair, "IsolatedWriter") as writer, \
                 patch.object(repair.subprocess, "Popen", return_value=self.process()), \
                 patch.object(repair, "process_start_ticks", return_value=123), \
                 patch.object(repair, "terminate_group", return_value=False), \
                 patch.object(repair.budget, "reserve"), \
                 patch.object(repair.budget, "release") as release:
                writer.return_value.worker = "c3-candidate-check-"+"a"*16
                writer.return_value.proxy = "c3-candidate-check-"+"b"*16
                writer.return_value.stop.return_value = True
                with self.assertRaisesRegex(RuntimeError, "cleanup.*unverified"):
                    repair.builder(root, case, {}, {}, [], root / "candidate", "test", time.time()+100)
                release.assert_not_called()
            self.assertEqual(len(list((root / "meta-active").glob("*.json"))), 1)
            self.assertEqual(DiscrepancyStore(root / "discrepancies.sqlite").rows("calls")[0]["status"], "failed")

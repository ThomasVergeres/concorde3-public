import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments import builder_worker
from experiments.isolated_writer import IsolatedWriter, worker_arguments, verify_host_isolation
from evals.lab import create_network
from experiments.discrepancy_transport import stop_active


class IsolatedWriterTests(unittest.TestCase):
    def test_default_internal_bridge_is_not_host_isolation(self):
        with patch("experiments.isolated_writer.command") as inspect:
            with self.assertRaisesRegex(RuntimeError, "gateway modes"):
                verify_host_isolation({"Internal": True, "Id": "a"*64, "Options": {}})
            inspect.assert_not_called()

    def test_isolated_modes_also_require_observed_addressless_bridge(self):
        network = {"Internal": True, "Id": "a"*64, "Options": {
            "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
            "com.docker.network.bridge.gateway_mode_ipv6": "isolated"}}
        for addresses in ([], [{"addr_info": [{"local": "10.0.0.1"}]}], [{"addr_info": [{"local": "fe80::1"}]}]):
            with self.subTest(addresses=addresses), patch("experiments.isolated_writer.command", return_value=json.dumps(addresses)):
                with self.assertRaisesRegex(RuntimeError, "bridge"):
                    verify_host_isolation(network)
        with patch("experiments.isolated_writer.command", return_value='[{"addr_info": []}]'):
            self.assertEqual(verify_host_isolation(network)[0], "br-"+"a"*12)

    def test_network_creation_requests_isolated_modes_only_when_opted_in(self):
        for isolate in (False, True):
            with self.subTest(isolate=isolate), patch("evals.lab.command", side_effect=["[]", "network-id"]) as execute:
                create_network("candidate-net", isolate_host=isolate)
                args = execute.call_args.args[0]
                self.assertIn("--internal", args)
                for family in ("ipv4", "ipv6"):
                    self.assertEqual("com.docker.network.bridge.gateway_mode_"+family+"=isolated" in args, isolate)

    def test_worker_mounts_only_explicit_candidate_session_worker_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/"source"; source.mkdir()
            sessions = root/"sessions"; sessions.mkdir()
            worker = root/"worker.py"; worker.write_text("# fixture")
            auth = root/"auth.json"; auth.write_text("{}")
            args = worker_arguments(source, sessions, worker, auth, "sha256:"+"a"*64,
                "private-net", "10.1.1.2", "c3-candidate-check-"+"b"*16)
            mounts = [args[i+1] for i,x in enumerate(args) if x == "--mount"]
            self.assertEqual(len(mounts), 4)
            self.assertTrue(all(f"source={root}," not in value for value in mounts))
            self.assertNotIn("docker.sock", " ".join(args))
            self.assertIn("--read-only", args)
            self.assertEqual(args[args.index("--network")+1], "private-net")
            self.assertEqual(args[args.index("--dns")+1], "127.0.0.1")
            self.assertIn("CONCORDE_ISOLATED_WRITER=1", args)
            with self.assertRaisesRegex(ValueError, "pinned"):
                worker_arguments(source, sessions, worker, auth, "unqualified:latest", "n", "ip", "c3-candidate-check-"+"b"*16)

    def test_no_host_worker_fallback_and_exact_subscription_profile(self):
        args = builder_worker.command()
        self.assertEqual(args[args.index("--model")+1], "gpt-5.6-sol")
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_reasoning_effort="xhigh"', args)
        self.assertIn("features.multi_agent=false", args)
        with patch.dict(os.environ, {}, clear=True), patch.object(builder_worker.os, "execvp") as execute:
            with self.assertRaisesRegex(RuntimeError, "isolated container"):
                builder_worker.main()
            execute.assert_not_called()

    def test_broad_source_and_elapsed_cutoff_are_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "dedicated"):
                IsolatedWriter(root, "call", root, "sha256:"+"a"*64, time.time()+100)
            writer = IsolatedWriter(root, "call", root/"cases/candidate", "sha256:"+"a"*64, time.time()-1)
            with patch("experiments.isolated_writer.command") as launch:
                with self.assertRaisesRegex(RuntimeError, "fixed cutoff"): writer.start()
                launch.assert_not_called()

    def test_setup_only_containers_are_closed_and_unknown_cleanup_is_retained(self):
        for verified in (True, False):
            with self.subTest(verified=verified), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); folder = root/"container-active"; folder.mkdir()
                inventory = folder/"call.json"
                names = ["c3-candidate-check-"+"a"*16, "c3-candidate-check-"+"b"*16]
                inventory.write_text(json.dumps({"call": "call", "containers": names}))
                with patch("experiments.discrepancy_transport.stop_candidate_container", return_value={"verified_stopped": verified}) as stop, \
                     patch("experiments.discrepancy_transport.budget.release") as release:
                    result = stop_active(root)
                    self.assertEqual(stop.call_count, 2)
                    self.assertEqual(inventory.exists(), not verified)
                    self.assertEqual(bool(result["errors"]), not verified)
                    self.assertEqual(release.call_count, int(verified))

    def test_container_closure_does_not_release_unverified_cli_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/"container-active").mkdir(); (root/"meta-active").mkdir()
            (root/"container-active/call.json").write_text(json.dumps({"call": "call", "containers": ["c3-candidate-check-"+"a"*16]}))
            (root/"meta-active/call.json").write_text(json.dumps({"call": "call", "pgid": 123, "process_start_ticks": 1}))
            with patch("experiments.discrepancy_transport.live_group_members", return_value={123: 1}), \
                 patch("experiments.discrepancy_transport.stop_owned_group", return_value=False), \
                 patch("experiments.discrepancy_transport.stop_candidate_container", return_value={"verified_stopped": True}), \
                 patch("experiments.discrepancy_transport.budget.release") as release:
                self.assertTrue(stop_active(root)["errors"])
                release.assert_not_called()
            self.assertTrue((root/"meta-active/call.json").exists())

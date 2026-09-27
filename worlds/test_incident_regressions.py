"""Mechanism reproductions from discrepancy campaign v2, not LLM evidence."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worlds import runtime, call_recovery
from worlds.engine import World
from worlds.store import Rejected
from worlds.test_world import Clock


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.world = World(Path(self.tmp.name) / "world", self.clock)
        self.world.create(hours=3)

    def test_ended_harness_slots_recover_without_refunding_admission(self):
        with self.world.s.transaction() as db:
            cfg = self.world.s.meta(db, "config"); cfg["concurrency"] = 3
            self.world.s.meta(db, "config", cfg)
        permits = [runtime.reserve(self.world, "reach", "act."+str(i), "work") for i in range(3)]
        with self.assertRaisesRegex(Rejected, "slots occupied"):
            runtime.reserve(self.world, "reach", "next", "work")
        state = {"activations": {"act."+str(i): {"status": "completed"} for i in range(3)}}
        with patch("worlds.budget.release") as release:
            runtime.reconcile_ended_calls(self.world, "reach", state, set(), {"verified": True})
            runtime.reconcile_ended_calls(self.world, "reach", state, set(), {"verified": True})
            self.assertEqual(release.call_count, 3)
        runtime.reserve(self.world, "reach", "next", "work")
        with self.world.s.transaction() as db:
            self.assertEqual(len(self.world.s.rows(db, "admission")), 4)
            for permit in permits:
                row = db.execute("SELECT status,body FROM calls WHERE id=?", (permit["id"],)).fetchone()
                self.assertEqual(row["status"], "failed")
                self.assertTrue(json.loads(row["body"])["censored"])

    def test_live_or_unverified_work_cannot_be_reclaimed_by_expiry(self):
        p = runtime.reserve(self.world, "reach", "act.live", "work")
        self.clock.now += 700
        state = {"activations": {"act.live": {"status": "completed"}}}
        runtime.reconcile_ended_calls(self.world, "reach", state, {"act.live"}, {"verified": True})
        with self.assertRaises(ValueError):
            runtime.reconcile_ended_calls(self.world, "reach", state, set(), {"verified": False})
        with self.world.s.transaction() as db:
            self.assertEqual(db.execute("SELECT status FROM calls WHERE id=?", (p["id"],)).fetchone()[0], "running")

    def test_delivery_based_refund_survives_delayed_delivery_and_revision(self):
        def act(actor, key, **data):
            return self.world.act(actor, key, data)["result"]
        offer = act("reach", "offer", op="offer", title="sample", price=1,
                    terms="Refund for 300 seconds after first delivery", delivery="artifact",
                    refund_seconds=300, refund_basis="first_delivery")
        contract = act("northstar", "buy", op="checkout", offer=offer["id"], agreed_price=1)
        self.clock.now += 400
        act("reach", "deliver", op="deliver", contract=contract["id"], reference="sample", reason="delivered")
        self.clock.now += 200
        act("reach", "revision", op="deliver", contract=contract["id"], reference="revision", reason="revision")
        refunded = act("northstar", "refund", op="refund", contract=contract["id"], reason="unsuitable")
        self.assertEqual(refunded["refunded"], 1)
        self.assertEqual(refunded["terms"]["refund_basis"], "first_delivery")

    def test_refund_controls_and_closure_use_same_clock(self):
        from worlds.forensics import contract_closure
        for basis in ("purchase", "first_delivery"):
            with self.subTest(basis=basis):
                def act(actor, key, **data):
                    return self.world.act(actor, basis+key, data)["result"]
                offer = act("reach", "offer", op="offer", title="sample", price=1,
                            terms="finite", delivery="artifact", refund_seconds=100, refund_basis=basis)
                c = act("northstar", "buy", op="checkout", offer=offer["id"], agreed_price=1)
                if basis == "first_delivery":
                    self.assertTrue(contract_closure(c, self.clock.now+999)["refund_entitlement_outlives_execution"])
                self.clock.now += 50
                act("reach", "deliver", op="deliver", contract=c["id"], reference="v1", reason="delivered")
                self.clock.now += 101
                c = act("reach", "revision", op="deliver", contract=c["id"], reference="v2", reason="revision")
                self.assertLess(contract_closure(c, self.clock.now)["refund_until"], self.clock.now)
                with self.assertRaisesRegex(Rejected, "agreed window"):
                    act("northstar", "refund", op="refund", contract=c["id"], reason="late")

    def test_real_process_scan_and_restart_recovery(self):
        state = {"seq": 1, "activations": {"act.witness": {"status": "failed"}}}
        path = Path(self.tmp.name)/"state.json"; path.write_text(json.dumps(state))
        permit = runtime.reserve(self.world, "reach", "act.witness", "work")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 env={**os.environ, "CONCORDE3_ACTIVATION": "act.witness"})
        proc_root = Path(self.tmp.name)/"proc"; proc_root.mkdir()
        (proc_root/str(child.pid)).symlink_to(Path("/proc")/str(child.pid))
        def scan():
            return json.loads(subprocess.check_output([sys.executable, "-c", call_recovery.SCAN, str(path), str(proc_root)], text=True))
        try:
            alive = scan()
            self.assertIn("act.witness", alive["live_activations"])
            self.assertEqual(runtime.reconcile_ended_calls(self.world, "reach", alive["state"], set(alive["live_activations"]), {"verified": True}), [])
        finally:
            child.kill(); child.wait(timeout=5)
        stopped = scan()
        restarted = World(self.world.s.root, self.clock)
        self.assertEqual(runtime.reconcile_ended_calls(restarted, "reach", stopped["state"], set(stopped["live_activations"]), {"verified": True}), [permit["id"]])
        repair = runtime.reserve(restarted, "reach", "act.witness", "rectification", execution="act.repair")
        runtime.finish(restarted, "reach", repair["id"], 0)
        with restarted.s.transaction() as db:
            self.assertEqual(len(restarted.s.rows(db, "admission")), 1)
            self.assertEqual(db.execute("SELECT status FROM calls WHERE id=?", (repair["id"],)).fetchone()[0], "completed")

    def test_shared_release_crash_is_reconciled_idempotently(self):
        ledger = Path(self.tmp.name)/"budget.jsonl"
        with patch.dict(os.environ, {"WORLD_BUDGET_FILE": str(ledger)}):
            permit = runtime.reserve(self.world, "reach", "act.crash", "work")
            with patch("worlds.budget.release", side_effect=OSError("crash between ledgers")):
                with self.assertRaises(OSError): runtime.finish(self.world, "reach", permit["id"], 0)
            restarted = World(self.world.s.root, self.clock)
            call_recovery.reconcile_shared(restarted); call_recovery.reconcile_shared(restarted)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["reserve", "release"])

    def test_actual_execution_id_protects_recovery_from_original_completion(self):
        permit = runtime.reserve(self.world, "reach", "act.original", "rectification", execution="act.recovery")
        state = {"activations": {"act.original": {"status": "failed"}, "act.recovery": {"status": "running", "recovery_of": "act.original"}}}
        self.assertEqual(runtime.reconcile_ended_calls(self.world, "reach", state, set(), {"verified": True}), [])
        with self.world.s.transaction() as db:
            self.assertEqual(db.execute("SELECT status FROM calls WHERE id=?", (permit["id"],)).fetchone()[0], "running")

    def test_offer_expiry_visible_without_mutating_contract(self):
        offer = self.world.act("reach", "offer", dict(op="offer", title="x", terms="x", delivery="x", price=1, expires_at=self.clock.now+10))["result"]
        contract = self.world.act("northstar", "buy", dict(op="checkout", offer=offer["id"], agreed_price=1))["result"]
        self.clock.now += 11
        views = [self.world.view("northstar", "offer")["records"],
                 self.world.view("northstar")["records"]["offer"],
                 self.world.act("northstar", "browse", dict(op="browse", kind="offer"))["result"]["records"],
                 [self.world.act("northstar", "inspect", dict(op="inspect", id=offer["id"]))["result"]]]
        for offers in views:
            current = next(o for o in offers if o["id"] == offer["id"])
            self.assertEqual(current["effective_status"], "expired")
            self.assertFalse(current["purchasable"])
        with self.world.s.transaction() as db:
            self.assertEqual(self.world.s.get(db, contract["id"]), contract)

    @unittest.skipUnless(os.environ.get("CONCORDE_DOCKER_WITNESS") == "1", "explicit Docker process witness")
    def test_real_container_scan_releases_only_absent_execution(self):
        instance = self.world.s.root/"subjects/reach"
        (instance/".concorde2").mkdir(parents=True)
        (instance/".concorde2/state.json").write_text(json.dumps({"seq": 1, "activations": {"act.container": {"status": "failed"}}}))
        name = "c3-recovery-witness-"+uuid.uuid4().hex[:10]
        def command(args):
            return subprocess.check_output(args, text=True, timeout=20)
        command(["docker", "run", "-d", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "32", "--mount", f"type=bind,src={instance},dst=/instance,readonly", "--entrypoint", "python3", "concorde3:discrepancy", "-c", "import time; time.sleep(90)"])
        try:
            inspected = json.loads(command(["docker", "inspect", name]))[0]
            manifest = {"subjects": {"reach": name}, "image": inspected["Image"]}
            command(["docker", "exec", "-d", "-e", "CONCORDE3_ACTIVATION=act.container", name, "python3", "-c", "import time; time.sleep(60)"])
            result = None
            for _ in range(20):
                result = call_recovery.inspect_subject(self.world, manifest, "reach")
                if "act.container" in result["live_activations"]: break
                time.sleep(.05)
            self.assertIn("act.container", result["live_activations"])
            permit = runtime.reserve(self.world, "reach", "act.container", "work")
            self.assertEqual(call_recovery.reconcile(self.world, manifest, "reach"), [])
            killer = "import pathlib,os,signal\nfor p in pathlib.Path('/proc').iterdir():\n if p.name.isdigit():\n  try: e=(p/'environ').read_bytes()\n  except FileNotFoundError: continue\n  if b'CONCORDE3_ACTIVATION=act.container' in e.split(b'\\0'): os.kill(int(p.name),signal.SIGKILL)"
            command(["docker", "exec", name, "python3", "-c", killer])
            for _ in range(20):
                released = call_recovery.reconcile(self.world, manifest, "reach")
                if released: break
                time.sleep(.05)
            self.assertEqual(released, [permit["id"]])
            self.assertEqual(call_recovery.reconcile(self.world, manifest, "reach"), [])
        finally:
            command(["docker", "rm", "-f", name])


if __name__ == "__main__": unittest.main()

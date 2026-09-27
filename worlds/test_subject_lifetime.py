"""Mechanical lifecycle witnesses, not evidence of business judgment."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from worlds import campaign
from worlds.engine import World
from worlds.forensics import write_json


class SubjectLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 1000000.
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("market", hours=1)
        with self.world.s.transaction() as db:
            self.cfg = self.world.s.meta(db, "config")
            self.cfg.update(counterpart_mode="scripted", cutoff=self.now + 3,
                            baseline_starts=3, maximum_starts=3)
            self.world.s.meta(db, "config", self.cfg)
        self.instance = self.world.s.root / "subjects/reach"
        self.state = {"seq": 1, "mode": "frozen", "config": {
            "model": self.cfg["model"], "effort": self.cfg["effort"], "starts_per_hour": 3},
            "activations": {}, "programs": {}}
        self.persist()
        self.inspect = {"Image": "sha256:qualified", "State": {
            "Running": False, "Restarting": False, "Status": "exited", "ExitCode": 0,
            "FinishedAt": "1970-01-12T13:46:39Z"}, "Mounts": [{"Type": "bind",
                "Destination": "/instance", "Source": str(self.instance)}]}
        self.manifest = {"subjects": {"reach": "seller"}, "containers": ["seller", "provider"],
                         "image": "sha256:qualified"}
        self.commands = []

    def persist(self):
        event = json.dumps({"seq": 1, "changes": [{"field": k, "value": v}
                          for k, v in self.state.items() if k != "seq"]})
        write_json(self.instance / ".concorde2/state.json", self.state)
        (self.instance / ".concorde2/events.jsonl").write_text(
            '{"event":' + event + ',"hash":"' + hashlib.sha256(event.encode()).hexdigest() + '"}\n')

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([self.inspect])
        if args[:2] == ["docker", "exec"]:
            raise subprocess.CalledProcessError(1, args, "container is not running")
        return ""

    def run_controller(self, tick=None, attach=False):
        def advance():
            if tick: tick()
            self.now += 1
        with patch.object(campaign, "launch", return_value=self.manifest), \
             patch.object(campaign, "command", side_effect=self.command), \
             patch.object(campaign, "freeze") as freeze, \
             patch.object(campaign, "report", return_value={}), \
             patch.object(campaign, "SubscriptionDriver"), \
             patch.object(campaign.time, "sleep"), \
             patch.object(self.world, "tick", side_effect=advance):
            campaign.run(self.world, attach=attach)
        return freeze

    def test_verified_seller_exit_keeps_provider_until_its_own_cutoff(self):
        offer = self.world.act("reach", "offer", {"op": "offer", "title": "Sample",
            "terms": "Fixed sample", "price": 3, "delivery": "artifact", "refund_seconds": 60})["result"]
        contract = self.world.act("northstar", "buy", {"op": "checkout", "offer": offer["id"],
            "agreed_price": 3})["result"]
        receipts = []
        def buyer():
            receipts.append(self.world.act("northstar", "return", {
                "op": "refund", "contract": contract["id"], "amount": 3, "reason": "Exercise agreed return"}))
        freeze = self.run_controller(buyer)
        self.assertEqual(self.now, self.cfg["cutoff"])
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(r == receipts[0] for r in receipts))
        self.assertFalse(any(c[:2] == ["docker", "exec"] for c in self.commands))
        self.assertEqual(freeze.call_count, 2)

    def test_nonzero_exit_is_not_silently_accepted(self):
        self.inspect["State"]["ExitCode"] = 1
        with self.assertRaisesRegex(RuntimeError, "exit cleanly"): self.run_controller()

    def test_unfrozen_state_is_not_silently_accepted(self):
        self.state["mode"] = "running"; self.persist()
        with self.assertRaisesRegex(ValueError, "frozen source"): self.run_controller()

    def test_wrong_container_mount_is_not_silently_accepted(self):
        self.inspect["Mounts"][0]["Source"] = "/unrelated"
        with self.assertRaisesRegex(RuntimeError, "provenance"): self.run_controller()

    def test_tampered_state_is_not_silently_accepted(self):
        write_json(self.instance / ".concorde2/state.json", {**self.state, "seq": 2})
        with self.assertRaisesRegex(ValueError, "journal"): self.run_controller()

    def test_pending_activation_and_program_rejected(self):
        for field, value in (("activations", {"a": {"status": "running"}}),
                             ("programs", {"p": {"enabled": True, "status": "stopped"}})):
            with self.subTest(field=field):
                self.state[field] = value; self.persist()
                with self.assertRaises(ValueError): self.run_controller()
                self.state[field] = {}

    def test_wrong_image_rejected(self):
        self.inspect["Image"] = "sha256:other"
        with self.assertRaisesRegex(RuntimeError, "provenance"): self.run_controller()

    def test_model_drift_rejected(self):
        self.state["config"]["model"] = "other"; self.persist()
        with self.assertRaisesRegex(RuntimeError, "model configuration"): self.run_controller()

    def test_exit_race_is_revalidated_not_restarted(self):
        original = self.command
        first = True
        def race(args, **kwargs):
            nonlocal first
            if args[:2] == ["docker", "inspect"] and first:
                first = False
                value = json.loads(json.dumps(self.inspect)); value["State"]["Running"] = True
                return json.dumps([value])
            return original(args, **kwargs)
        self.command = race
        self.run_controller()
        self.assertEqual(self.now, self.cfg["cutoff"])
        self.assertEqual(sum(c[:2] == ["docker", "exec"] for c in self.commands), 1)

    def test_live_subject_keeps_existing_configuration_checks(self):
        original = self.command
        self.inspect["State"]["Running"] = True
        def live(args, **kwargs):
            if args[:2] == ["docker", "exec"]:
                self.commands.append(args)
                return json.dumps(self.state["config"])
            return original(args, **kwargs)
        self.command = live
        self.run_controller()
        self.assertEqual(sum(c[:2] == ["docker", "exec"] for c in self.commands), 3)

    def test_missing_container_remains_failure(self):
        def missing(args, **kwargs):
            raise subprocess.CalledProcessError(1, args)
        self.command = missing
        with self.assertRaises(subprocess.CalledProcessError): self.run_controller()

    def test_attach_preserves_stopped_seller_and_requires_live_provider(self):
        self.manifest["status"] = "running"
        write_json(self.world.s.root / "deployment.json", self.manifest)
        original = self.command
        def attached(args, **kwargs):
            if args[:3] == ["docker", "inspect", "provider"]:
                self.commands.append(args)
                return "true"
            return original(args, **kwargs)
        self.command = attached
        self.run_controller(attach=True)
        self.assertEqual(self.now, self.cfg["cutoff"])
        self.assertTrue(any(c[:3] == ["docker", "inspect", "provider"] for c in self.commands))

    def test_configuration_write_exit_race_is_revalidated(self):
        original = self.command
        self.inspect["State"]["Running"] = True
        def race(args, **kwargs):
            if args[:2] == ["docker", "exec"] and "call" in args:
                return json.dumps({**self.state["config"], "starts_per_hour": 2})
            if args[:2] == ["docker", "exec"] and "configure" in args:
                self.inspect["State"]["Running"] = False
            return original(args, **kwargs)
        self.command = race
        self.run_controller()
        self.assertEqual(self.now, self.cfg["cutoff"])
        self.assertEqual(sum("configure" in c for c in self.commands), 1)

    def test_attach_rejects_stopped_provider(self):
        self.manifest["status"] = "running"
        write_json(self.world.s.root / "deployment.json", self.manifest)
        original = self.command
        def attached(args, **kwargs):
            if args[:3] == ["docker", "inspect", "provider"]: return "false"
            return original(args, **kwargs)
        self.command = attached
        with self.assertRaisesRegex(RuntimeError, "missing live component: provider"):
            self.run_controller(attach=True)

    def test_terminal_subject_releases_only_its_interrupted_call_slots(self):
        own = self.world.reserve_call("reach", "subject", 60)["id"]
        other = self.world.reserve_call("steward", "subject", 60)["id"]
        buyer = self.world.reserve_call("northstar", "counterpart", 60)["id"]
        self.world.finish_call(own, "running", {"basis": "constructed interrupted phase"})
        with patch("worlds.budget.release") as release:
            self.run_controller()
        with self.world.s.transaction() as db:
            rows = {r["id"]: dict(r) for r in db.execute("SELECT * FROM calls")}
        self.assertEqual(rows[own]["status"], "failed")
        evidence = json.loads(rows[own]["body"])
        self.assertTrue(evidence["process_stopped"])
        self.assertTrue(evidence["censored"])
        self.assertNotIn("usage", evidence)
        self.assertEqual(rows[other]["status"], "reserved")
        self.assertEqual(rows[buyer]["status"], "reserved")
        release.assert_called_once_with(own)

    def test_reconciliation_requires_terminal_proof_and_verifier_is_read_only(self):
        own = self.world.reserve_call("reach", "subject", 60)["id"]
        with patch.object(campaign, "command", side_effect=self.command), \
             patch("worlds.budget.release") as release:
            campaign.terminal_subject(self.world, self.manifest, "reach", self.cfg)
            self.inspect["State"]["Running"] = True
            self.assertIsNone(campaign.reconcile_terminal_subject(
                self.world, self.manifest, "reach", self.cfg))
            self.inspect["State"]["Running"] = False
            self.state["mode"] = "running"; self.persist()
            with self.assertRaisesRegex(ValueError, "frozen source"):
                campaign.reconcile_terminal_subject(self.world, self.manifest, "reach", self.cfg)
            release.assert_not_called()
        with self.world.s.transaction() as db:
            self.assertEqual(db.execute("SELECT status FROM calls WHERE id=?", (own,)).fetchone()[0], "reserved")

    def test_pending_statuses_reconciled_idempotently_without_refunding_hourly_budget(self):
        ids = [self.world.reserve_call("reach", "subject", 60)["id"] for _ in range(3)]
        self.world.finish_call(ids[1], "running", {"partial": "retained"})
        self.world.finish_call(ids[2], "uncertain", {"transport": "unknown"})
        with patch.object(campaign, "command", side_effect=self.command), \
             patch("worlds.budget.release") as release:
            for _ in range(2):
                campaign.reconcile_terminal_subject(self.world, self.manifest, "reach", self.cfg)
            self.assertEqual(release.call_count, 3)
        with self.world.s.transaction() as db:
            rows = list(db.execute("SELECT * FROM calls WHERE status!='undispatched'"))
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(r["status"] == "failed" for r in rows))
            evidence = {r["id"]: json.loads(r["body"]) for r in rows}
            self.assertEqual(evidence[ids[1]]["prior_evidence"], {"partial": "retained"})
            self.assertEqual(evidence[ids[2]]["prior_evidence"], {"transport": "unknown"})

    def test_completion_race_keeps_terminal_usage_and_outcome(self):
        own = self.world.reserve_call("reach", "subject", 60)["id"]
        finish = self.world.finish_call
        evidence = {"usage": {"input_tokens": 17}, "exit_code": 0}
        def race(identity, status, body):
            finish(identity, "completed", evidence)
            return finish(identity, status, body)
        with patch.object(campaign, "command", side_effect=self.command), \
             patch.object(self.world, "finish_call", side_effect=race), \
             patch("worlds.budget.release") as release:
            campaign.reconcile_terminal_subject(self.world, self.manifest, "reach", self.cfg)
            campaign.reconcile_terminal_subject(self.world, self.manifest, "reach", self.cfg)
            release.assert_called_once_with(own)
        with self.world.s.transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (own,)).fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertEqual(json.loads(row["body"]), evidence)

    def test_unrelated_disposition_error_is_not_hidden(self):
        from worlds.store import Rejected
        self.world.reserve_call("reach", "subject", 60)
        with patch.object(campaign, "command", side_effect=self.command), \
             patch.object(self.world, "finish_call", side_effect=Rejected("bad evidence")):
            with self.assertRaisesRegex(Rejected, "bad evidence"):
                campaign.reconcile_terminal_subject(self.world, self.manifest, "reach", self.cfg)


if __name__ == "__main__": unittest.main()

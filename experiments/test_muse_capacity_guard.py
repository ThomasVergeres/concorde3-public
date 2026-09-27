import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from experiments.muse_capacity_guard import Guard, SLOTS


class CapacityGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir()
        self.ledger = self.ledger_dir / "admissions.jsonl"
        self.ledger.write_text('{"id":"preserved","invocations":9}\n')
        self.slots = self.ledger_dir / "model-slots"
        self.slots.mkdir()
        self.now = [1000.0]
        self.guards = []

    def tearDown(self):
        for guard in self.guards:
            guard.close()
        self.temp.cleanup()

    def guard(self, inspect=lambda container: False, duration=10):
        value = Guard(self.ledger, self.root / ("evidence-" + str(len(self.guards))),
                      self.now[0] + duration, inspect=inspect, clock=lambda: self.now[0])
        self.guards.append(value)
        return value

    def test_active_flock_skipped_no_preemption_and_later_acquired(self):
        with (self.slots / "6").open("a+") as active:
            active.write('{"container":"current"}')
            active.flush()
            fcntl.flock(active, fcntl.LOCK_EX)
            guard = self.guard()
            guard.tick()
            self.assertNotIn(6, guard.held)
            self.assertEqual(guard.waiting["6"], "existing_flock_held")
            self.assertEqual(len(guard.held), 9)
        guard.tick()
        self.assertEqual(set(guard.held), set(SLOTS))

    def test_live_container_without_flock_not_taken_metadata_preserved(self):
        metadata = b'{ "container": "still-live", "extra": "unaltered" }\n'
        (self.slots / "8").write_bytes(metadata)
        running = [True]
        seen = []
        def inspect(name):
            seen.append(name)
            return running[0]
        guard = self.guard(inspect)
        guard.tick()
        self.assertNotIn(8, guard.held)
        self.assertEqual(guard.waiting["8"], "previous_container_still_running")
        self.assertEqual((self.slots / "8").read_bytes(), metadata)
        running[0] = False
        guard.tick()
        self.assertIn(8, guard.held)
        self.assertEqual((self.slots / "8").read_bytes(), metadata)
        self.assertEqual(seen, ["still-live", "still-live"])

    def test_unknown_container_state_or_bad_metadata_never_claimed(self):
        (self.slots / "6").write_text('{"container":"unknown"}')
        (self.slots / "7").write_text('{invalid')
        def inspect(name):
            raise RuntimeError("cannot establish previous slot container state")
        guard = self.guard(inspect)
        guard.tick()
        self.assertNotIn(6, guard.held)
        self.assertNotIn(7, guard.held)
        self.assertEqual(guard.waiting["6"], "inspection_unavailable")

    def test_expiry_releases_all_and_never_touches_ledger_or_low_slots(self):
        ledger_before = self.ledger.read_bytes()
        for index in range(6):
            (self.slots / str(index)).write_text("lower-slot-sentinel")
        guard = self.guard()
        guard.tick()
        for fd in guard.held.values():
            self.assertFalse(os.get_inheritable(fd))
        self.now[0] += 11
        self.assertFalse(guard.tick())
        self.assertEqual(guard.held, {})
        self.assertTrue(guard.closed)
        for index in SLOTS:
            with (self.slots / str(index)).open() as slot:
                fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.ledger.read_bytes(), ledger_before)
        self.assertTrue(all((self.slots / str(index)).read_text() == "lower-slot-sentinel" for index in range(6)))
        state = json.loads((guard.evidence / "state.json").read_text())
        self.assertEqual(state["event"], "expired")
        self.assertEqual(state["held"], [])
        self.assertEqual(guard.evidence.stat().st_mode & 0o777, 0o700)

    def test_finite_bound_and_fresh_evidence_enforced(self):
        for duration in (0, -1, 10801):
            with self.assertRaises(ValueError):
                self.guard(duration=duration)
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaises(ValueError):
            Guard(self.ledger, existing, 1010, clock=lambda: 1000)

    def test_symlink_slot_cannot_write_outside_explicit_locations(self):
        outside = self.root / "outside"
        outside.write_text("preserve")
        (self.slots / "7").symlink_to(outside)
        before = outside.stat().st_mtime_ns
        guard = self.guard()
        guard.tick()
        self.assertNotIn(7, guard.held)
        self.assertEqual(outside.read_text(), "preserve")
        self.assertEqual(outside.stat().st_mtime_ns, before)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"ledger", "outside", "evidence-0"})

    def test_nonregular_slot_never_blocks_or_is_claimed(self):
        os.mkfifo(self.slots / "9")
        guard = self.guard()
        guard.tick()
        self.assertNotIn(9, guard.held)
        self.assertEqual(guard.waiting["9"], "nonregular_slot_path")

    def test_cli_signal_releases_all_held_flocks(self):
        import datetime as dt
        evidence = self.root / "process-evidence"
        until = dt.datetime.fromtimestamp(time.time() + 20, dt.timezone.utc).isoformat()
        process = subprocess.Popen([sys.executable, "-m", "experiments.muse_capacity_guard",
            "--ledger", str(self.ledger), "--evidence", str(evidence), "--until", until],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                if (evidence / "state.json").exists():
                    if len(json.loads((evidence / "state.json").read_text())["held"]) == 10:
                        break
                time.sleep(.05)
            else:
                self.fail("guard did not acquire empty test slots")
            process.terminate()
            process.wait(timeout=5)
            for index in SLOTS:
                with (self.slots / str(index)).open() as slot:
                    fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = json.loads((evidence / "state.json").read_text())
            self.assertTrue(state["closed"])
            self.assertEqual(state["held"], [])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()

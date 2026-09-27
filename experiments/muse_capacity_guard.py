"""Finite, nonpreemptive experiment-only hold of existing lab slots 6 through 15.

This reduces future episode admission to six only as higher slots become genuinely
free. It never edits slot metadata, model state, invocations or behavioral grades.
Do not launch without documenting the experiment capacity amendment.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import time

from evals.lab import container_running

SLOTS = tuple(range(6, 16))


class StopGuard(BaseException):
    pass


def parse_until(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expiry must include timezone")
    return parsed.timestamp()


def atomic(path, value):
    fd, name = tempfile.mkstemp(prefix=".guard-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def no_symlinks(path):
    return not path.is_symlink() and not any(parent.is_symlink() for parent in path.parents)


class Guard:
    def __init__(self, ledger, evidence, until, inspect=container_running, clock=time.time):
        self.clock, self.inspect, self.until = clock, inspect, until
        self.started = clock()
        if not 0 < until - self.started <= 3 * 3600:
            raise ValueError("expiry must be future and no more than three hours away")
        ledger, evidence = Path(ledger).absolute(), Path(evidence).absolute()
        if not no_symlinks(ledger) or not ledger.parent.is_dir():
            raise ValueError("explicit ledger directory must exist without symlinks")
        self.directory = ledger.parent / "model-slots"
        if not no_symlinks(self.directory):
            raise ValueError("slot directory cannot be a symlink")
        if not no_symlinks(evidence) or evidence.exists() or not evidence.parent.is_dir():
            raise ValueError("evidence must be a fresh directory under an existing nonsymlink parent")
        if evidence == self.directory or evidence.is_relative_to(self.directory):
            raise ValueError("evidence must not occupy lab slot paths")
        self.evidence = evidence
        self.evidence.mkdir(mode=0o700)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.held = {}
        self.waiting = {str(index): "not_checked" for index in SLOTS}
        self.closed = False
        self.record("started")

    def record(self, event):
        value = {"at": self.clock(), "event": event, "pid": os.getpid(), "started_at": self.started,
                 "until": self.until, "target_slots": list(SLOTS), "held": sorted(self.held),
                 "waiting": dict(self.waiting), "closed": self.closed,
                 "meaning": "No preemption or slot metadata edits. Held flock is the authority; a stale state file is not proof the guard is alive."}
        with (self.evidence / "journal.jsonl").open("a") as output:
            os.chmod(self.evidence / "journal.jsonl", 0o600)
            output.write(json.dumps(value, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        atomic(self.evidence / "state.json", value)

    def tick(self):
        if self.closed:
            return False
        if self.clock() >= self.until:
            self.close("expired")
            return False
        before = (sorted(self.held), dict(self.waiting))
        for index in SLOTS:
            if index in self.held:
                continue
            if self.clock() >= self.until:
                self.close("expired")
                return False
            fd = None
            keep = False
            reason = "inspection_unavailable"
            try:
                path = self.directory / str(index)
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    reason = "nonregular_slot_path"
                    continue
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    reason = "existing_flock_held"
                    continue
                # Read only after acquiring flock: metadata belongs to the last
                # owner and must remain byte-for-byte intact even when stale.
                data = os.read(fd, 65537)
                if len(data) > 65536:
                    reason = "oversized_metadata"
                    continue
                if data.strip():
                    previous = json.loads(data)
                    if not isinstance(previous, dict) or not isinstance(previous.get("container"), str):
                        reason = "invalid_metadata"
                        continue
                    if self.inspect(previous["container"]):
                        reason = "previous_container_still_running"
                        continue
                if self.clock() >= self.until:
                    reason = "expired_during_inspection"
                    continue
                self.held[index] = fd
                self.waiting.pop(str(index), None)
                keep = True
            except (OSError, ValueError, RuntimeError, KeyError):
                reason = "inspection_unavailable"
            finally:
                if fd is not None and not keep:
                    os.close(fd)
                if not keep:
                    self.waiting[str(index)] = reason
        if before != (sorted(self.held), self.waiting):
            self.record("hold_set_changed")
        if self.clock() >= self.until:
            self.close("expired")
            return False
        return True

    def close(self, reason="stopped"):
        if self.closed:
            return
        try:
            for fd in self.held.values():
                os.close(fd)
        finally:
            self.held.clear()
            self.waiting = {}
            self.closed = True
            self.record(reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, help="Existing lab ledger; content is never read or modified")
    parser.add_argument("--evidence", required=True, help="Fresh private evidence directory")
    parser.add_argument("--until", required=True, help="UTC/RFC3339 terminal expiry, at most three hours")
    args = parser.parse_args(argv)
    until = parse_until(args.until)
    guard = None
    previous = {}

    def stop(signum, frame):
        raise StopGuard()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            previous[sig] = signal.signal(sig, stop)
        guard = Guard(args.ledger, args.evidence, until)
        signal.setitimer(signal.ITIMER_REAL, max(.001, until - time.time()))
        while guard.tick():
            time.sleep(min(2, max(.001, until - time.time())))
    except StopGuard:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if guard is not None:
            guard.close("expired" if time.time() >= until else "signal_or_exit")
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()

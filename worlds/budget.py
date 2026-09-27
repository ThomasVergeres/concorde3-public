"""Shared conservative dispatch budget across world processes and containers."""
import fcntl
import json
import os
from pathlib import Path
import time
from .store import require


def path():
    return Path(os.environ.get("WORLD_BUDGET_FILE", str(Path.home()/".local/share/concorde/world-budget/calls.jsonl")))


def reserve(identity, file=None, now=None, cap=200, concurrency=None):
    file = Path(file) if file else path()
    now = time.time() if now is None else now
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file.open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        rows = [json.loads(line) for line in f if line.strip()]
        reservations = [r for r in rows if r.get("kind", "reserve") == "reserve"]
        released = {r["id"] for r in rows if r.get("kind") == "release"}
        if any(r["id"] == identity for r in reservations):
            require(identity not in released, "dispatch reservation already released")
            return
        require(sum(r["at"] > now-3600 for r in reservations) < cap, "host-wide world dispatch budget exhausted")
        if concurrency is not None:
            require(sum(r.get("active", False) and r["id"] not in released for r in reservations) < concurrency, "shared model slots occupied")
        f.seek(0, 2)
        f.write(json.dumps({"kind": "reserve", "id": identity, "at": now, "active": concurrency is not None})+"\n")
        f.flush()
        os.fsync(f.fileno())


def release(identity, file=None):
    """Verified terminal call; no expiry-based reclaim of possibly live work."""
    return release_many([identity], file=file)


def release_many(identities, file=None):
    """Idempotently reconcile terminal DB receipts after a crash between ledgers."""
    file = Path(file) if file else path()
    if not file.exists():
        return
    with file.open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        rows = [json.loads(line) for line in f if line.strip()]
        released = {r["id"] for r in rows if r.get("kind") == "release"}
        active = {r["id"] for r in rows if r.get("active")}
        pending = (set(identities) & active) - released
        if not pending: return
        f.seek(0, 2)
        for identity in sorted(pending):
            f.write(json.dumps({"kind": "release", "id": identity, "at": time.time()})+"\n")
        f.flush()
        os.fsync(f.fileno())

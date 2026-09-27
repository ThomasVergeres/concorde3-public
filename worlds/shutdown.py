"""Self-contained cutoff watchdog, snapshotted outside the changing source tree."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .engine import World
from .observe import report
from .store import Rejected, require


def command(args):
    return subprocess.check_output(args, text=True, timeout=60).strip()


def finish_stopped_call(world, identity, evidence):
    """Caller verifies process stop; a racing terminal writer keeps its evidence."""
    require(evidence.get("process_stopped") is True, "verified process stop required")
    try:
        world.finish_call(identity, "failed", evidence)
    except Rejected as error:
        if str(error) != "call already terminal":
            raise
        with world.s.transaction() as db:
            row = db.execute("SELECT status FROM calls WHERE id=?", (identity,)).fetchone()
        if row is None or row["status"] not in ("completed", "failed", "undispatched"):
            raise


def freeze(world):
    root = world.s.root.resolve()
    label = "concorde.world="+hashlib.sha256(str(root).encode()).hexdigest()[:12]
    path = root/"deployment.json"
    manifest = json.loads(path.read_text()) if path.exists() else {"subjects": {}}
    # Stop processes even if a damaged store prevents recording the gate.
    try:
        world.freeze()
    finally:
        names = command(["docker", "ps", "-a", "--filter", "label="+label, "--format", "{{.Names}}"]).splitlines()
        for container in manifest["subjects"].values():
            if container in names:
                subprocess.run(["docker", "exec", container, "concorde3", "freeze", "/instance"], capture_output=True, timeout=20)
        if names:
            command(["docker", "stop", "-t", "3", *names])
        running = command(["docker", "ps", "--filter", "label="+label, "--format", "{{.Names}}"]).splitlines()
        if running:
            raise RuntimeError("world containers still running: "+str(running))
    with world.s.transaction() as db:
        pending = [r["id"] for r in db.execute("SELECT id FROM calls WHERE status IN ('reserved','running','uncertain')")]
    for identity in pending:
        finish_stopped_call(world, identity, {"process_stopped": True, "reason": "World freeze; all labeled containers verified stopped", "censored": True})
    from .budget import release
    with world.s.transaction() as db:
        identities = [r[0] for r in db.execute("SELECT id FROM calls")]
    for identity in identities:
        release(identity)
    manifest.update(status="frozen", stopped_containers=names)
    path.write_text(json.dumps(manifest, indent=2)+"\n")
    (root/"final-report.json").write_text(json.dumps(report(world), indent=2)+"\n")


if __name__ == "__main__":
    freeze(World(Path(sys.argv[1]).resolve()))

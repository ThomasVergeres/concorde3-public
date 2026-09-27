"""Private quiescent world snapshots and isolated lived-state continuations.

Not process checkpointing or historical deterministic replay. Sources must already
be frozen; this tool never freezes, resumes or changes a source instance.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import stat
import subprocess
import tempfile
import time

from .forensics import capture_file, write_json, state_matches_replay
from .store import encoded

LIMIT = 32 * 1024 * 1024
EXCLUDED = {"auth.json", "access.json", "company-preflight.json", "lock"}


def regular(path):
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError("snapshot requires regular files, not links/devices: " + str(path))


def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or str(p) != name:
        raise ValueError("unsafe snapshot path")
    if any(x in EXCLUDED or x.startswith(".env") for x in p.parts):
        raise ValueError("credential/ephemeral path in snapshot")
    return p


def quiescent(state):
    if state.get("mode") != "frozen":
        raise ValueError("subject must already be frozen")
    if any(a.get("status") == "running" for a in state.get("activations", {}).values()):
        raise ValueError("in-flight activation cannot be replayed as quiescent")
    # First supported boundary has no managed programs. Copying process state,
    # sockets or an old endpoint into a new network would be false fidelity.
    if state.get("programs"):
        raise ValueError("program checkpoint unsupported; require an explicit restart contract first")


def running(root):
    label = "concorde.world=" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return subprocess.check_output(["docker", "ps", "--filter", "label="+label,
                                   "--format", "{{.Names}}"], text=True, timeout=20).splitlines()


def attention_boundary(state):
    """Describe retained dispositions, not a duplicate of kernel eligibility."""
    intentions = {key: item.get("attention",{}).get("effort_state","unknown")
                  for key,item in state.get("items",{}).items()
                  if item.get("kind")=="intention" and item.get("status")=="active"}
    return {"intentions":intentions,
            "dormant":[key for key,value in intentions.items() if value=="dormant"],
            "meaning":"Preserved dispositions, not guaranteed admission. New mail alone does not wake dormant intentions."}


def deployment_subjects(deployment):
    """Exact stored-self inventory; inactive copies are not deployed cognition."""
    active, inactive = deployment["subjects"], deployment.get("inactive_subjects", [])
    if not isinstance(active, dict) or not isinstance(inactive, list):
        raise ValueError("invalid deployment subject inventory")
    names = [*active, *inactive]
    if any(not isinstance(n, str) or safe_name(n).parts != (n,) for n in names) or len(set(names)) != len(names):
        raise ValueError("unsafe, overlapping or duplicate deployment subjects")
    return names


def capture(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or target.is_relative_to(source):
        raise ValueError("fresh independent snapshot directory required")
    if running(source):
        raise ValueError("source world has running containers")
    deployment = json.loads((source/"deployment.json").read_text())
    if deployment.get("status") != "frozen":
        raise ValueError("source deployment not frozen")
    subjects = deployment_subjects(deployment)
    stored = {p.parent.parent.name for p in (source/"subjects").glob("*/.concorde2/state.json")}
    if stored != set(subjects):
        raise ValueError("stored selves differ from declared deployment inventory")
    target.mkdir(parents=True, mode=0o700)
    manifest = {"version": 1, "kind": "quiescent-world-fork", "source": str(source),
                "started": time.time(), "image": deployment["image"], "subjects": {}, "files": [],
                "executed_subjects": list(deployment["subjects"]),
                "inactive_subjects": deployment.get("inactive_subjects", []),
                "limitations": ["Restart/fork boundary, not a historical activation replay.",
                    "No live programs supported; process RAM, sessions and subscription credentials excluded.",
                    "Original absolute dates and event history retained; fork grants a new finite execution window.",
                    "Frozen-cutoff behavior remains in memory; this may affect decisions.",
                    "Private simulated world access tokens exist in the captured DB; rotate before use. Never publish raw bundle."]}
    try:
        with tempfile.TemporaryDirectory(prefix="c3-replay-db-") as temp:
            database = Path(temp)/"world.sqlite"
            with sqlite3.connect(f"file:{source/'world.sqlite'}?mode=ro", uri=True) as db, sqlite3.connect(database) as out:
                if json.loads(db.execute("SELECT body FROM meta WHERE key='frozen'").fetchone()[0]) is not True:
                    raise ValueError("source database not frozen")
                if db.execute("SELECT count(*) FROM calls WHERE status IN ('reserved','running','uncertain')").fetchone()[0]:
                    raise ValueError("unsettled model execution")
                db.backup(out)
                manifest["world_watermark"] = out.execute("SELECT max(seq) FROM events").fetchone()[0]
            ref = capture_file(target, database, "world.sqlite", 256*1024*1024)
            if ref["truncated"]: raise ValueError("database too large")
            manifest["database"] = ref
        for actor in subjects:
            if safe_name(actor).parts != (actor,): raise ValueError("unsafe actor ID")
            instance = source/"subjects"/actor
            if instance.is_symlink(): raise ValueError("linked instance")
            before = (instance/".concorde2/state.json").read_bytes()
            state = json.loads(before)
            quiescent(state)
            # Verify canonical event prefix, not just parse the mutable snapshot.
            from .forensics import replay
            history = (instance/".concorde2/events.jsonl").read_bytes()
            reconstructed = replay(history, state["seq"])
            if not state_matches_replay(state, reconstructed):
                raise ValueError("state/event prefix mismatch")
            manifest["subjects"][actor] = {"sequence": state["seq"], "nodes": len(state["nodes"]),
                "items": len(state["items"]), "activations": len(state["activations"]),
                "state_sha256": hashlib.sha256(before).hexdigest()}
            manifest["subjects"][actor]["attention_boundary"] = attention_boundary(state)
            for path in sorted(instance.rglob("*")):
                if path.is_symlink(): raise ValueError("symlink in instance: " + str(path.relative_to(instance)))
                if path.is_dir(): continue
                rel = path.relative_to(source).as_posix()
                if any(x in EXCLUDED or x.startswith(".env") for x in path.relative_to(instance).parts): continue
                safe_name(rel)
                regular(path)
                ref = capture_file(target, path, rel, LIMIT)
                if ref["truncated"]: raise ValueError("file too large for faithful fork: " + rel)
                manifest["files"].append(ref)
            if before != (instance/".concorde2/state.json").read_bytes() or history != (instance/".concorde2/events.jsonl").read_bytes():
                raise ValueError("subject changed while capturing")
        with sqlite3.connect(f"file:{source/'world.sqlite'}?mode=ro", uri=True) as db:
            if db.execute("SELECT max(seq) FROM events").fetchone()[0] != manifest["world_watermark"]:
                raise ValueError("world changed while capturing")
        if running(source): raise ValueError("source resumed while capturing")
        for ref in manifest["files"]:
            original=source/ref["path"]
            regular(original)
            if hashlib.sha256(original.read_bytes()).hexdigest()!=ref["sha256"]:
                raise ValueError("workspace changed while capturing")
        manifest["finished"] = time.time()
        write_json(target/"manifest.json", manifest)
        return {"snapshot": str(target), "subjects": manifest["subjects"], "qualified": True,
                "meaning": "quiescent restart boundary only"}
    except BaseException as error:
        write_json(target/"capture-error.json", {"error": str(error), "qualified": False})
        raise


def load(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot/"manifest.json").read_text())
    if manifest.get("version") != 1 or manifest.get("kind") != "quiescent-world-fork":
        raise ValueError("unsupported snapshot")
    seen = set()
    for ref in [manifest["database"], *manifest["files"]]:
        name = str(safe_name(ref["path"]))
        if name in seen: raise ValueError("duplicate snapshot target")
        seen.add(name)
        if ref.get("truncated") or ref["blob"] != "blobs/"+ref["sha256"]:
            raise ValueError("invalid or incomplete snapshot blob")
        if len(ref["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in ref["sha256"]):
            raise ValueError("invalid digest")
        path = snapshot/ref["blob"]
        regular(path)
        if not path.resolve().is_relative_to(snapshot): raise ValueError("blob escapes snapshot")
        data = path.read_bytes()
        if len(data) != ref["captured_bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError("snapshot integrity failure")
    return manifest


def fork(snapshot, target, fixture, hours=1, model="gpt-5.6-terra", effort="medium", starts=3, acknowledge_dormancy=False):
    snapshot, target = Path(snapshot).resolve(), Path(target).resolve()
    manifest = load(snapshot)
    boundaries={}
    for actor in manifest["subjects"]:
        refs=[r for r in manifest["files"] if r["path"]==f"subjects/{actor}/.concorde2/state.json"]
        if len(refs)!=1: raise ValueError("missing subject state")
        boundaries[actor]=attention_boundary(json.loads((snapshot/refs[0]["blob"]).read_bytes()))
    if any(b["dormant"] for b in boundaries.values()) and not acknowledge_dormancy:
        raise ValueError("retained dormant intentions: explicitly acknowledge preservation before forking; any owner reentry must be separately recorded")
    if target.exists() or target.is_relative_to(snapshot) or target.is_relative_to(Path(manifest["source"])):
        raise ValueError("fresh isolated fork directory required")
    if not math.isfinite(hours) or not 0 < hours <= 6 or not 1 <= starts <= 6:
        raise ValueError("bounded fork horizon/cap required")
    if (model, effort) not in (("gpt-5.6-luna", "xhigh"), ("gpt-5.6-terra", "medium")):
        raise ValueError("explicit qualified profile required")
    target.mkdir(parents=True, mode=0o700)
    now, cutoff = time.time(), time.time()+hours*3600
    provenance = {"version": 1, "snapshot_sha256": hashlib.sha256((snapshot/"manifest.json").read_bytes()).hexdigest(),
        "source": manifest["source"], "snapshot": str(snapshot), "subjects": manifest["subjects"],
        "started": now, "cutoff": cutoff, "model": model, "effort": effort, "starts": starts,
        "attention_boundary":boundaries, "dormancy_acknowledged":acknowledge_dormancy,
        "status": "preparing", "transformations": ["New world tokens/endpoints; old tokens not usable in this fork.",
            "New finite execution window, selected model and start ceiling; original timestamps retained.",
            "Past sessions and processes are not resumed. Canonical history and working files retained.",
            "World calendar origin/period remain unchanged; elapsed offline time can advance circumstances, not retroactively execute missed work.",
            "Finalized counterpart continuations may become due with elapsed wall time; no forced subject wake."]}
    write_json(target/"fork.json", provenance)
    try:
        for ref in [manifest["database"], *manifest["files"]]:
            path = target/ref["path"]
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as f:
                f.write((snapshot/ref["blob"]).read_bytes()); f.flush(); os.fsync(f.fileno())
            # Retain a recorded owner-executable working artifact. Never replay
            # setuid/setgid or public access bits into the private continuation.
            path.chmod(0o600 | (ref.get("source_mode", 0o600) & 0o100))
        from .engine import World
        world = World(target)
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg.update(cutoff=cutoff, model=model, effort=effort,
                       baseline_starts=starts, maximum_starts=starts, calls_per_hour=60,
                       concurrency=3, counterpart_calls_per_hour=3, counterpart_response_reserve=2,
                       experiment="lived-readiness-fork-v1")
            world.s.meta(db, "config", cfg)
            world.s.meta(db, "frozen", False)
            world.s.meta(db, "endpoints", {})
            for row in list(db.execute("SELECT id FROM actors")):
                db.execute("UPDATE actors SET token=? WHERE id=?", (secrets.token_urlsafe(32), row[0]))
            world.s.event(db, "_operator", "isolated_fork", {"source_manifest": provenance["snapshot_sha256"],
                "transformations": provenance["transformations"], "config": cfg})
        for actor in manifest["subjects"]:
            spec = {"cutoff": cutoff, "model": model, "effort": effort, "starts": starts,
                    "source_manifest": provenance["snapshot_sha256"]}
            subprocess.run([str(Path(fixture).resolve()), "fork", str(target/"subjects"/actor)],
                           input=json.dumps(spec), text=True, check=True, capture_output=True, timeout=30)
        provenance["status"] = "prepared"
        write_json(target/"fork.json", provenance)
        return provenance
    except BaseException as error:
        provenance.update(status="failed", error=str(error))
        write_json(target/"fork.json", provenance)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("capture", "verify", "fork"))
    p.add_argument("source", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--fixture", type=Path)
    p.add_argument("--hours", type=float, default=1)
    p.add_argument("--model", choices=("luna-xhigh", "terra-medium"), default="terra-medium")
    p.add_argument("--starts", type=int, default=3)
    p.add_argument("--acknowledge-dormancy", action="store_true", help="preserve dormant intentions consciously; does NOT wake them")
    a = p.parse_args()
    if a.action == "verify": result = {"qualified": True, "subjects": load(a.source)["subjects"]}
    else:
        if not a.output: p.error("--output required")
        if a.action == "capture": result = capture(a.source, a.output)
        else:
            if not a.fixture: p.error("--fixture required")
            model, effort = ("gpt-5.6-luna", "xhigh") if a.model == "luna-xhigh" else ("gpt-5.6-terra", "medium")
            result = fork(a.source, a.output, a.fixture, a.hours, model, effort, a.starts, a.acknowledge_dormancy)
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()

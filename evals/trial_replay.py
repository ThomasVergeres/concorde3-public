"""Private frozen file-interface lab copies, never a process/activation replay.

No model call, source write, program execution or unfreeze. Preparing a runnable
continuation is a separate, provenance-bearing fixture action. Dependency and
network qualification still require an isolated witness in the pinned image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import time

from evals.trial_evidence import capture_trial
from worlds.forensics import root_path, read_regular, frozen_state, write_json

LIMIT = 64 * 1024 * 1024
EPHEMERAL = {"lock", "supervisor.lock", "lab-start.json", "lab-stop.json"}
RUNTIME_FILES = {"state.json", "events.jsonl"}
RUNTIME_DIRS = {"contexts", "harness-logs", "program-logs", "rejected", "harness-sessions", "muse-sessions"}


def disjoint(a, b):
    return not (a == b or a.is_relative_to(b) or b.is_relative_to(a))


def safe_path(name):
    if not isinstance(name, str): raise ValueError("invalid snapshot path")
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or ".." in p.parts or str(p) != name or p.parts[0] not in {"subject", "exchange"}:
        raise ValueError("invalid snapshot path")
    for index, part in enumerate(p.parts):
        if part.lower() in {"auth.json", "auth.jsonl", "subscription-auth.json", "subscription-auth.jsonl"}:
            raise ValueError("credential path unsupported")
        if part.startswith(".") and not (index == 1 and part == ".concorde2" and p.parts[0] == "subject"):
            raise ValueError("hidden dependency unsupported: " + name)
    if len(p.parts) > 2 and p.parts[:2] == ("subject", ".concorde2"):
        if p.parts[2] not in RUNTIME_FILES | RUNTIME_DIRS:
            raise ValueError("runtime dependency unsupported: " + name)
    return p


def stopped_container(trial, manifest):
    identity = manifest.get("id", "")
    if not re.fullmatch(r"[a-z0-9]{1,64}", identity): raise ValueError("invalid trial ID")
    # Missing container is not proof that the source's execution is stopped.
    obj = json.loads(subprocess.check_output(
        ["docker", "inspect", "c3-lab-" + identity], text=True, timeout=20))[0]
    if obj["State"]["Running"] or obj["State"].get("Restarting"):
        raise ValueError("source container is running")
    mounts = {m["Destination"]: Path(m["Source"]).resolve() for m in obj["Mounts"] if m["Type"] == "bind"}
    if mounts.get("/instance") != trial / "subject" or mounts.get("/exchange") != trial / "exchange":
        raise ValueError("container does not own these source mounts")
    if obj["Image"] != manifest.get("image_id", manifest.get("image")):
        raise ValueError("source image provenance mismatch")
    return {"id": obj["Id"], "image": obj["Image"], "finished": obj["State"]["FinishedAt"]}


def inventory(trial):
    files, directories, excluded = {}, {}, []
    count, total = 0, 0
    pending = [trial / "subject", trial / "exchange"]
    while pending:
        path = pending.pop()
        count += 1
        if count > 4096: raise ValueError("source inventory bound exceeded")
        name = path.relative_to(trial).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode): raise ValueError("symlink dependency unsupported")
        if path.parent == trial / "subject/.concorde2" and path.name in EPHEMERAL:
            if not stat.S_ISREG(info.st_mode): raise ValueError("invalid ephemeral runtime file")
            excluded.append(name)
            continue
        safe_path(name)
        mode = stat.S_IMODE(info.st_mode)
        if mode & ~0o777: raise ValueError("privileged file/directory mode unsupported")
        if stat.S_ISDIR(info.st_mode):
            directories[name] = mode
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(pending) + count >= 4096: raise ValueError("source inventory bound exceeded")
                    pending.append(Path(entry.path))
        else:
            data, info = read_regular(path, min(16 * 1024 * 1024, LIMIT - total))
            total += len(data)
            if len(files) >= 512: raise ValueError("source file bound exceeded")
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "source_mode": stat.S_IMODE(info.st_mode),
                           "captured_bytes": len(data), "mtime_ns": info.st_mtime_ns}
    return {"files": files, "directories": directories, "excluded": sorted(excluded)}


def capture(trial, audit, label):
    trial, audit = root_path(trial), root_path(audit)
    if not disjoint(trial, audit): raise ValueError("disjoint audit required")
    original_manifest, _ = read_regular(trial / "manifest.json")
    identity = json.loads(original_manifest)
    before_container = stopped_container(trial, identity)
    before = inventory(trial)
    frozen_state(read_regular(trial / "subject/.concorde2/state.json")[0],
                 read_regular(trial / "subject/.concorde2/events.jsonl")[0])
    result = capture_trial(trial, audit, label)
    if result["errors"] or result["omitted"] or any(f["truncated"] for f in result["files"]):
        raise ValueError("incomplete observation cannot qualify")
    refs = [r for r in result["files"] if r["path"].startswith(("subject/", "exchange/"))]
    recorded = {r["path"]: {k: r[k] for k in ("sha256", "source_mode", "captured_bytes", "mtime_ns")} for r in refs}
    if recorded != before["files"] or inventory(trial) != before:
        raise ValueError("source changed or capture omitted dependency")
    if read_regular(trial / "manifest.json")[0] != original_manifest or stopped_container(trial, identity) != before_container:
        raise ValueError("source execution/provenance changed")
    target = audit / "rounds" / label / "qualified.json"
    record = {"version": 1, "kind": "frozen-file-lab", "source": str(trial),
              "container": before_container, "files": refs, "directories": before["directories"],
              "excluded": before["excluded"], "qualified_at": time.time(),
              "observation_sha256": hashlib.sha256((target.parent / "manifest.json").read_bytes()).hexdigest(),
              "limitations": ["Quiescent restart boundary, not atomic historical activation/process replay.",
                  "Only subject/exchange files; observer facts and original trial controller not restored.",
                  "Modes and file mtimes retained; ownership, directory mtimes, ACLs/xattrs and process RAM not restored.",
                  "Original graph/history/times preserved; no automatic wake or attention reset.",
                  "Pinned-image/dependency and new isolated effects require a separate runtime witness."]}
    # Validate the captured bytes themselves before publishing qualification.
    validate(record, audit)
    write_json(target, record)
    return target


def validate(manifest, audit):
    if manifest.get("version") != 1 or manifest.get("kind") != "frozen-file-lab": raise ValueError("unsupported snapshot")
    refs, size = {}, 0
    if len(manifest["files"]) > 512 or len(manifest["directories"]) > 4096:
        raise ValueError("snapshot inventory bound exceeded")
    for name, mode in manifest["directories"].items():
        safe_path(name)
        if type(mode) is not int or not 0 <= mode <= 0o777: raise ValueError("invalid directory mode")
    for ref in manifest["files"]:
        name = str(safe_path(ref["path"]))
        if name in refs or name in manifest["directories"]: raise ValueError("duplicate snapshot path")
        digest = ref.get("sha256", "")
        mode = ref.get("source_mode")
        if type(mode) is not int or not 0 <= mode <= 0o777: raise ValueError("source mode required")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or ref.get("blob") != "blobs/" + digest or ref.get("truncated"):
            raise ValueError("invalid/incomplete blob")
        data, _ = read_regular(audit / ref["blob"], 16 * 1024 * 1024)
        size += len(data)
        if size > LIMIT or len(data) != ref.get("captured_bytes") or len(data) != ref.get("source_bytes") or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("snapshot integrity failure")
        if type(ref.get("mtime_ns")) is not int: raise ValueError("source mtime required")
        refs[name] = data
    if any(name not in refs for name in ("subject/.concorde2/state.json", "subject/.concorde2/events.jsonl")):
        raise ValueError("missing canonical state/history")
    for name in [*refs, *manifest["directories"]]:
        if any(str(p) in refs for p in PurePosixPath(name).parents): raise ValueError("file/directory collision")
    frozen_state(refs["subject/.concorde2/state.json"], refs["subject/.concorde2/events.jsonl"])
    return refs


def load(snapshot):
    snapshot = root_path(snapshot)
    manifest = json.loads(read_regular(snapshot, 1024 * 1024)[0])
    audit = snapshot.parent.parent.parent
    validate(manifest, audit)
    return manifest


def restore(snapshot, target):
    snapshot, target = root_path(snapshot), root_path(target)
    manifest = load(snapshot)
    audit, source = snapshot.parent.parent.parent, root_path(manifest["source"])
    if target.exists() or not disjoint(target, audit) or not disjoint(target, source):
        raise ValueError("fresh disjoint target required")
    # Read verified bytes once before touching destination. Retain failed partial
    # copies for diagnosis; never launch them or replace an existing target.
    data = validate(manifest, audit)
    target.mkdir(parents=True, mode=0o700)
    for name in sorted(manifest["directories"], key=lambda n: len(PurePosixPath(n).parts)):
        (target / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    for ref in manifest["files"]:
        path = target / ref["path"]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as f:
            f.write(data[ref["path"]]); f.flush(); os.fsync(f.fileno())
        path.chmod(ref["source_mode"])
        os.utime(path, ns=(ref["mtime_ns"], ref["mtime_ns"]))
    for name, mode in sorted(manifest["directories"].items(), key=lambda n: -len(PurePosixPath(n[0]).parts)):
        (target / name).chmod(mode)
    proof = {"status": "frozen-copy", "source": str(source), "snapshot": str(snapshot),
             "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
             "files": len(data), "restored_at": time.time(), "limitations": manifest["limitations"]}
    write_json(target / "restore.json", proof)
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "verify", "restore"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label")
    args = parser.parse_args()
    if args.action == "capture":
        if not args.output or not args.label: parser.error("--output and --label required")
        print(capture(args.source, args.output, args.label))
    elif args.action == "restore":
        if not args.output: parser.error("--output required")
        print(json.dumps(restore(args.source, args.output), indent=2))
    else:
        result = load(args.source)
        print(json.dumps({"qualified": True, "scope": result["kind"], "files": len(result["files"])}))

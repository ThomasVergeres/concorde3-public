"""Private, bounded lab evidence copies; never a subject action or atomic restore.

Reuses the world forensic blob and journal tools. Generated code is copied as
data, never imported/executed. No model calls, Docker calls, or instance writes.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time

from worlds.forensics import capture_file, replay, write_json


def capture_trial(trial, audit, label, *, maximum_files=512,
                  maximum_bytes=64*1024*1024, per_file=16*1024*1024,
                  maximum_entries=4096):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", label):
        raise ValueError("invalid evidence label")
    if any(type(n) is not int or n <= 0 for n in
           (maximum_files, maximum_bytes, per_file, maximum_entries)):
        raise ValueError("capture bounds must be positive integers")
    trial, audit = Path(trial).absolute(), Path(audit).absolute()
    for path in (trial, audit):
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("capture roots must not traverse symlinks")
    trial, audit = trial.resolve(), audit.resolve()
    if audit == trial or audit.is_relative_to(trial) or trial.is_relative_to(audit):
        raise ValueError("audit and trial must be disjoint")
    if not trial.is_dir() or not (trial / "manifest.json").is_file():
        raise ValueError("a lab trial with manifest.json is required")
    destination = audit / "rounds" / label
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("refusing to replace an evidence snapshot") from None
    r = {"label": label, "trial": str(trial), "started": time.time(),
         "files": [], "errors": [], "captured_bytes": 0, "omitted": False,
         "limits": {"files": maximum_files, "bytes": maximum_bytes,
                    "per_file": per_file, "entries": maximum_entries},
         "journal_prefix": {"verified": False},
         "limitations": [
             "Files observed at different times; not an atomic workspace/world checkpoint.",
             "Verified journal prefix is canonical graph state, not past workspace contents or process memory.",
             "Hidden workspace trees and known credential files excluded; ordinary files/transcripts may still contain secrets.",
             "All captures are private. Encrypted reasoning is unavailable; no private thought inferred."]}
    scanned = 0

    def allowed(name):
        return not name.startswith(".") and name.lower() not in {
            "auth.json", "auth.jsonl", "subscription-auth.json", "subscription-auth.jsonl"}

    def copy(path):
        relative = str(path.relative_to(trial))
        if len(r["files"]) >= maximum_files or r["captured_bytes"] >= maximum_bytes:
            r["omitted"] = True
            return
        try:
            if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(trial)):
                raise ValueError("symlink source rejected")
            entry = capture_file(audit, path, relative,
                                 limit=min(per_file, maximum_bytes - r["captured_bytes"]))
            r["files"].append(entry)
            r["captured_bytes"] += entry["captured_bytes"]
        except (OSError, ValueError) as error:
            r["errors"].append({"path": relative, "error": str(error)})

    def walk(directory):
        nonlocal scanned
        pending = [directory]
        while pending:
            directory = pending.pop()
            if not directory.exists() and not directory.is_symlink():
                continue
            if any(p.is_symlink() for p in (directory, *directory.parents) if p.is_relative_to(trial)):
                r["errors"].append({"path": str(directory.relative_to(trial)), "error": "symlink directory rejected"})
                continue
            try:
                with os.scandir(directory) as entries:
                    while True:
                        if scanned >= maximum_entries or len(r["files"]) >= maximum_files or r["captured_bytes"] >= maximum_bytes:
                            # Conservatively mark possible omissions without
                            # reading further entries merely to count them.
                            r["omitted"] = True
                            return
                        entry = next(entries, None)
                        if entry is None:
                            break
                        scanned += 1
                        if not allowed(entry.name):
                            continue
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
                        else:
                            copy(path)
            except OSError as error:
                r["errors"].append({"path": str(directory.relative_to(trial)), "error": str(error)})

    # Canonical state then journal: the later event read can normally cover the
    # earlier state watermark even while the live instance continues committing.
    runtime = trial / "subject/.concorde2"
    for path in (runtime / "state.json", runtime / "events.jsonl", trial / "manifest.json"):
        copy(path)
    for name in ("seed.json", "facts.json", "execution.json", "result.json",
                 "systemization-observation.json", "lifelike-observation.json"):
        if (trial / name).exists(): copy(trial / name)
    walk(trial / "subject")  # Hidden runtime is visited only through the allowlist below.
    walk(trial / "exchange")
    for folder in ("contexts", "harness-logs", "program-logs", "rejected", "harness-sessions", "muse-sessions"):
        walk(runtime / folder)
    captured = {entry["path"]: entry for entry in r["files"]}
    try:
        state_entry = captured["subject/.concorde2/state.json"]
        journal_entry = captured["subject/.concorde2/events.jsonl"]
        if state_entry["truncated"]:
            raise ValueError("state capture truncated")
        state = json.loads((audit / state_entry["blob"]).read_bytes())
        replay((audit / journal_entry["blob"]).read_bytes(), state["seq"])
        r["journal_prefix"] = {"sequence": state["seq"], "verified": True}
    except (KeyError, ValueError, OSError) as error:
        r["journal_prefix"]["error"] = str(error)
    r["scanned_entries"] = scanned
    r["finished"] = time.time()
    write_json(destination / "manifest.json", r)
    return r


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path)
    parser.add_argument("audit", type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    result = capture_trial(args.trial, args.audit, args.label)
    print(json.dumps({key: result[key] for key in
                     ("label", "started", "finished", "captured_bytes", "omitted", "journal_prefix", "errors")}, indent=2))

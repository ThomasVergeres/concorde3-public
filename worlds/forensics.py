"""Read-only, private, content-addressed evidence snapshots for behavioral review.

No model calls, no world actions, no edits to a Concorde or its continuation.
Snapshots are time-bounded observations, not globally atomic distributed state.
"""
import argparse
import base64
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time

LIMIT = 16*1024*1024


def root_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("roots must not traverse symlinks")
    return path.resolve()


def read_regular(path, limit=64 * 1024 * 1024):
    root_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("bounded regular file required")
        data = f.read(limit + 1)
        if len(data) != info.st_size or len(data) > limit:
            raise ValueError("file changed or exceeded bound")
    return data, info


def frozen_state(state_data, history):
    state = json.loads(state_data)
    if state.get("mode") != "frozen": raise ValueError("frozen source required")
    for kind in ("activations", "programs"):
        for value in state.get(kind, {}).values():
            if value.get("status") == "running" or any(value.get("process", {}).values()):
                raise ValueError("live or retained process handle")
            if kind == "programs" and (value.get("enabled") or value.get("status") != "stopped"):
                raise ValueError("programs must be disabled/stopped")
    if not history.endswith(b"\n") or len(history.splitlines()) != state.get("seq"):
        raise ValueError("complete exact journal required")
    reconstructed = replay(history, state["seq"])
    if not state_matches_replay(state, reconstructed):
        raise ValueError("state/journal mismatch")
    return state


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w") as f:
        json.dump(value, f, indent=2); f.flush(); os.fsync(f.fileno())
    path.chmod(0o600)


def blob(root, data):
    digest = hashlib.sha256(data).hexdigest()
    path = root/"blobs"/digest
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        with path.open("xb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        path.chmod(0o600)
    return {"sha256": digest, "blob": "blobs/"+digest, "captured_bytes": len(data)}


def capture_file(root, path, name, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        data = f.read(limit)
    return {"path": name, "source_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "source_mode": stat.S_IMODE(st.st_mode),
            "truncated": st.st_size>len(data), **blob(root, data)}


def persistent_sessions(audit, instance, actor, per_file=LIMIT, maximum_files=256,
                        maximum_bytes=64*1024*1024, maximum_entries=4096):
    """Bounded host-side capture, including after the subject container stops.

    None means a legacy deployment without a session bind. Omitted/truncated
    records stay explicit; this does not reconstruct missing private reasoning.
    """
    if any(type(n) is not int or n <= 0 for n in (per_file, maximum_files, maximum_bytes, maximum_entries)):
        raise ValueError("session capture bounds must be positive integers")
    audit, instance = Path(audit), Path(instance)
    root = instance / ".concorde2/harness-sessions"
    for path in (instance, instance / ".concorde2", root):
        if path.is_symlink():
            raise ValueError("persistent session storage must not traverse a symlink")
    if not root.exists():
        return None
    if not root.is_dir():
        raise ValueError("persistent session storage is not a directory")
    result = {"source": "instance_private_session_bind", "sessions": [], "captured_bytes": 0,
              "omitted": False, "errors": [], "maximum_files": maximum_files,
              "maximum_bytes": maximum_bytes, "per_file_bytes": per_file,
              "maximum_entries": maximum_entries}
    inspected = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        kept = []
        for name in sorted(directories):
            inspected += 1
            if inspected > maximum_entries:
                result["omitted"] = True
                return result
            path = Path(directory) / name
            if path.is_symlink():
                result["errors"].append({"path": str(path.relative_to(root)), "error": "symlink session directory rejected"})
            else:
                kept.append(name)
        directories[:] = kept
        for name in sorted(files):
            inspected += 1
            if inspected > maximum_entries:
                result["omitted"] = True
                return result
            # Session JSONL only, never the separate Codex authentication file
            # or known credential-file lookalikes placed inside this directory.
            if not name.endswith(".jsonl") or name in ("auth.jsonl", "subscription-auth.jsonl") or name.startswith(".env"):
                continue
            path = Path(directory) / name
            if path.is_symlink():
                result["errors"].append({"path": str(path.relative_to(root)), "error": "symlink session file rejected"})
                continue
            if len(result["sessions"]) >= maximum_files or result["captured_bytes"] >= maximum_bytes:
                result["omitted"] = True
                return result
            try:
                entry = capture_file(audit, path, str(Path("/home/node/.codex/sessions") / path.relative_to(root)),
                                     limit=min(per_file, maximum_bytes - result["captured_bytes"]))
                result["sessions"].append({**entry, "actor": actor, "storage": "instance_private_session_bind"})
                result["captured_bytes"] += entry["captured_bytes"]
            except (OSError, ValueError) as error:
                result["errors"].append({"path": str(path.relative_to(root)), "error": str(error)})
    return result


def snapshot(cohort, audit, number, scheduled=None, sessions=True):
    cohort, audit = Path(cohort).resolve(), Path(audit).resolve()
    audit.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = audit/"rounds"/f"{number:02d}.json"
    if target.exists():
        raise ValueError("refusing to replace an evidence snapshot")
    record = {"number": number, "scheduled": scheduled, "started": time.time(), "worlds": {}, "errors": [],
        "limitations": ["World SQLite backup is consistent per world, not simultaneous with C3 state or other worlds.",
            "C3 JSON snapshots are atomic; event/log files may contain an incomplete final line.",
            "Retains emitted reasoning/summary records only; unexposed reasoning is unavailable, not reconstructed.",
            "Missing/unfinished usage is unknown, not zero. In-flight harness logs may only appear at phase exit."]}
    metadata = json.loads((cohort/"cohort.json").read_text())
    for name, location in metadata["worlds"].items():
        world = Path(location)
        entry = {"started": time.time(), "files": [], "sessions": []}
        record["worlds"][name] = entry
        temp = audit/(f"snapshot-{number}-{name}.sqlite")
        try:
            with sqlite3.connect(f"file:{world/'world.sqlite'}?mode=ro", uri=True) as source, sqlite3.connect(temp) as dest:
                source.backup(dest)
                entry["event_watermark"] = dest.execute("SELECT max(seq) FROM events").fetchone()[0]
            entry["database"] = capture_file(audit, temp, "world.sqlite", limit=256*1024*1024)
            temp.unlink()  # Only this function's explicit temporary backup.
            selected = [world/"deployment.json"]
            for instance in (world/"subjects").iterdir():
                if not instance.is_dir() or instance.is_symlink(): continue
                runtime = instance/".concorde2"
                selected += [instance/"brain.json", runtime/"state.json", runtime/"events.jsonl", runtime/"company-preflight.json"]
                for folder in ("contexts", "harness-logs", "program-logs", "rejected", "muse-sessions"):
                    selected += list((runtime/folder).glob("*"))
                for path in instance.rglob("*"):
                    rel = path.relative_to(instance)
                    if any(part.startswith(".") for part in rel.parts) or path.is_dir() or path.is_symlink(): continue
                    if path.name in ("auth.json",) or path.name.startswith(".env"): continue
                    selected.append(path)
            for path in (world/"calls").glob("*/*"):
                if path.name in ("model.json", "provenance.json", "prompt.txt", "response.json", "schema.json", "api-response.json"):
                    selected.append(path)
            # Read event tails last so earlier state/packet sequence watermarks
            # are normally covered even while a live activation commits.
            for path in sorted(set(selected), key=lambda p: (p.name == "events.jsonl", str(p))):
                if not path.exists(): continue
                try: entry["files"].append(capture_file(audit, path, str(path.relative_to(world))))
                except Exception as error: record["errors"].append({"path": str(path), "error": str(error)})
            if sessions and (world/"deployment.json").exists():
                deployment = json.loads((world/"deployment.json").read_text())
                for actor, container in deployment["subjects"].items():
                    try:
                        saved = persistent_sessions(audit, world / "subjects" / actor, actor)
                    except (OSError, ValueError) as error:
                        record["errors"].append({"actor": actor, "error": str(error), "scope": "persistent session collection"})
                        continue
                    if saved is not None:
                        entry["sessions"].extend(saved.pop("sessions"))
                        entry.setdefault("session_capture", {})[actor] = saved
                        record["errors"].extend({**error, "actor": actor, "scope": "persistent session collection"}
                                                for error in saved["errors"])
                        continue
                    # Only this instance's session transcripts, never auth.json.
                    script = """import base64,json,pathlib,os,stat
for p in pathlib.Path('/home/node/.codex/sessions').rglob('*.jsonl'):
 if p.is_symlink(): continue
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  st=os.fstat(f.fileno())
  if not stat.S_ISREG(st.st_mode): continue
  data=f.read(16777216)
 print(json.dumps({'path':str(p),'source_bytes':st.st_size,'data':base64.b64encode(data).decode()}))
"""
                    result = subprocess.run(["docker", "exec", container, "python3", "-c", script], capture_output=True, text=True, timeout=20)
                    if result.returncode:
                        record["errors"].append({"container": container, "error": result.stderr[:300], "scope": "session collection"})
                    for line in result.stdout.splitlines():
                        r = json.loads(line); raw = base64.b64decode(r.pop("data"))
                        entry["sessions"].append({**r, "actor": actor, "truncated": len(raw)<r["source_bytes"], **blob(audit, raw)})
        except Exception as error:
            record["errors"].append({"world": name, "error": str(error)})
        entry["finished"] = time.time()
    record["finished"] = time.time()
    write_json(target, record)
    with (audit/"snapshots.jsonl").open("a") as f:
        f.write(json.dumps({"number": number, "scheduled": scheduled, "started": record["started"], "finished": record["finished"], "manifest": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "errors": len(record["errors"])})+"\n")
        f.flush(); os.fsync(f.fileno())
    return record


def events(data):
    result, errors = [], []
    for line, text in enumerate(data.decode(errors="replace").splitlines(), 1):
        try: result.append(json.loads(text))
        except ValueError: errors.append(line)
    return result, errors


def state_matches_replay(state, reconstructed):
    """Compare C3 values without rewriting raw historical evidence.

    Go v2 Source.observed_at uses time.Time's zero sentinel for an unknown time.
    Old encoders emit it; current encoders omit it. Only that field in canonical
    item sources has this representation equivalence. This does not normalize
    arbitrary dates, known instants, source lists or other optional fields.
    """
    left, right = state, reconstructed
    if state.get("version") == reconstructed.get("version") == 2:
        left, right = copy.deepcopy(state), copy.deepcopy(reconstructed)
        for value in (left, right):
            for item in value.get("items", {}).values():
                for source in item.get("sources", []):
                    if source.get("observed_at") == "0001-01-01T00:00:00Z":
                        del source["observed_at"]
    # Replay initializes historical fields absent from some snapshots. Preserve
    # the existing empty/default-field compatibility; nonempty omissions fail.
    return (all(right.get(k) == v for k,v in left.items()) and
            not any(v and k not in left for k,v in right.items()))


def replay(data, sequence):
    """Reconstruct canonical C3 state at a recorded sequence; never its live store."""
    state = {field: {} for field in ("nodes", "edges", "items", "consequences", "timers", "wakes",
                                     "activations", "programs", "receipts", "credits", "waits")}
    state.update(version=0, seq=0, config={}, mode="", starts=0, last_urgent=0)
    expected = 1
    decoder = json.JSONDecoder()
    for line in data.decode().splitlines():
        envelope = json.loads(line)
        event = envelope["event"]
        if event["seq"]>sequence: break
        # Hash the original event bytes, not a differently serialized Python map.
        start = line.index('"event"') + len('"event"')
        start = line.index(':', start) + 1
        while line[start].isspace(): start += 1
        _, end = decoder.raw_decode(line, start)
        if hashlib.sha256(line[start:end].encode()).hexdigest() != envelope["hash"]:
            raise ValueError("event checksum mismatch")
        if event["seq"] != expected:
            raise ValueError("non-contiguous event sequence")
        expected += 1
        for change in event["changes"]:
            if "key" in change:
                collection = state.setdefault(change["field"], {})
                if change.get("delete"): collection.pop(change["key"], None)
                else: collection[change["key"]] = change.get("value")
            else: state[change["field"]] = change.get("value")
        state["seq"] = event["seq"]
        if event["seq"] == sequence: break
    if state.get("seq") != sequence:
        raise ValueError("requested state sequence is not present in captured event prefix")
    return state


def digest_round(audit, number):
    audit = Path(audit)
    record = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    result = {"number": number, "started": record["started"], "errors": record["errors"], "subjects": {}}
    for world, entry in record["worlds"].items():
        for f in entry["files"]:
            if not f["path"].endswith("/.concorde2/state.json"): continue
            s = json.loads((audit/f["blob"]).read_text()); actor = f["path"].split("/")[1]
            result["subjects"][world+"/"+actor] = {"sequence": s["seq"], "activations": [{k:a.get(k) for k in
                ("id", "intention", "status", "phase", "reason", "started", "finished", "summary", "work_summary", "usage", "completion", "recovery_of")}
                for a in s["activations"].values()], "programs": s.get("programs", {}), "timers": s.get("timers", {}),
                "consequences": s.get("consequences", {})}
    write_json(audit/"digests"/f"{number:02d}.json", result)
    return result


def compare_round(audit, number):
    """Evidence delta for human review. No automated competence verdict."""
    audit = Path(audit)
    current = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    previous = json.loads((audit/"rounds"/f"{number-1:02d}.json").read_text()) if number else {"worlds": {}}
    result = {"number": number, "capture_errors": current["errors"], "worlds": {}}
    for name, entry in current["worlds"].items():
        last = previous["worlds"].get(name, {}).get("event_watermark", 0) or 0
        summary = {"watermark": entry.get("event_watermark"), "events": [], "subjects": {},
                   "truncated": [f["path"] for f in entry["files"]+entry["sessions"] if f.get("truncated")]}
        result["worlds"][name] = summary
        with sqlite3.connect(f"file:{audit/entry['database']['blob']}?mode=ro", uri=True) as db:
            summary["calls"] = db.execute("SELECT status,count(*) FROM calls GROUP BY status").fetchall()
            summary["event_counts"] = db.execute("SELECT kind,count(*) FROM events WHERE seq>? GROUP BY kind", (last,)).fetchall()
            for seq, at, actor, kind, body in db.execute("SELECT seq,at,actor,kind,body FROM events WHERE seq>? ORDER BY seq", (last,)):
                if kind in ("exposure", "call_reserved", "call_running", "call_completed", "subject_call_reserved", "tick", "consumed"):
                    continue
                summary["events"].append({"seq": seq, "at": at, "actor": actor, "kind": kind, "body": json.loads(body)})
        old_files = {f["path"]: f for f in previous["worlds"].get(name, {}).get("files", [])}
        for f in entry["files"]:
            if not f["path"].endswith("/.concorde2/state.json"): continue
            state = json.loads((audit/f["blob"]).read_text())
            old_file = old_files.get(f["path"])
            before = json.loads((audit/old_file["blob"]).read_text()) if old_file else {"activations": {}}
            changed = [a for k,a in state["activations"].items() if a != before["activations"].get(k)]
            summary["subjects"][f["path"].split('/')[1]] = {"seq": state["seq"],
                "changed_activations": [{k:a.get(k) for k in ("id", "status", "phase", "started", "finished", "summary", "work_summary", "usage", "completion")} for a in changed],
                "activation_count": len(state["activations"]), "program_count": len(state.get("programs", {}))}
    write_json(audit/"deltas"/f"{number:02d}.json", result)
    return result


def activation_bundle(audit, number, world, actor, activation):
    """Link a decision to served packets, verified pre-state, outputs and session."""
    audit = Path(audit)
    manifest = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    entry = manifest["worlds"][world]
    files = {f["path"]: f for f in entry["files"]}
    prefix = f"subjects/{actor}/.concorde2/"
    state = json.loads((audit/files[prefix+"state.json"]["blob"]).read_text())
    selected = state["activations"][activation]
    history = (audit/files[prefix+"events.jsonl"]["blob"]).read_bytes()
    result = {"round": number, "world": world, "actor": actor, "activation": selected,
              "manifest": f"rounds/{number:02d}.json", "phases": {}, "sessions": [], "logs": [],
              "reasoning": {"records": 0, "readable_summaries": 0,
                            "limitation": "Encrypted reasoning is opaque; counts do not expose its contents."}}
    for phase in ("work", "rectification", "work.return1", "rectification.return1"):
        name = prefix+f"contexts/{activation}.{phase}.json"
        if name not in files: continue
        packet = json.loads((audit/files[name]["blob"]).read_text())
        result["phases"][phase] = {"packet": files[name], "pre_state": replay(history, packet["sequence"])}
    result["logs"] = [f for name,f in files.items() if name.startswith(prefix) and activation in name and "/contexts/" not in name]
    for session in entry["sessions"]:
        if session["actor"] != actor: continue
        rows, errors = events((audit/session["blob"]).read_bytes())
        meta = next((r["payload"] for r in rows if r.get("type") == "session_meta"), {})
        if selected.get("session") not in (meta.get("id"), meta.get("session_id")): continue
        result["sessions"].append({**session, "incomplete_lines": errors})
        for row in rows:
            payload = row.get("payload", {})
            if row.get("type") == "response_item" and payload.get("type") == "reasoning":
                result["reasoning"]["records"] += 1
                result["reasoning"]["readable_summaries"] += bool(payload.get("summary"))
    # All identifiers were resolved from the manifest/state, not used to open live paths.
    name = hashlib.sha256((world+"/"+actor+"/"+activation).encode()).hexdigest()[:16]
    target = audit/"bundles"/f"{number:02d}-{name}.json"
    write_json(target, result)
    return {"bundle": str(target), "activation": activation, "phases": list(result["phases"]),
            "session_count": len(result["sessions"]), "reasoning": result["reasoning"]}


def decision_bundle(audit, number, world, sequence):
    """Companion to activation_bundle, using counterpart ledger call provenance."""
    audit = Path(audit)
    manifest = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    entry = manifest["worlds"][world]
    with sqlite3.connect(f"file:{audit/entry['database']['blob']}?mode=ro", uri=True) as db:
        row = db.execute("SELECT at,actor,kind,body FROM events WHERE seq=?", (sequence,)).fetchone()
        if row is None or row[2] != "decision": raise ValueError("not a captured counterpart decision")
        call = db.execute("""SELECT c.id,c.body,e.seq FROM events e JOIN calls c
            ON c.id=json_extract(e.body,'$.id') WHERE e.kind='call_completed'
            AND c.actor=? AND c.category='counterpart' AND e.seq<? ORDER BY e.seq DESC LIMIT 1""", (row[1], sequence)).fetchone()
        if call is None: raise ValueError("no preceding completed counterpart call")
    files = [f for f in entry["files"] if f["path"].startswith("calls/"+call[0]+"/")]
    result = {"round": number, "world": world, "actor": row[1], "event": sequence, "at": row[0],
              "decision": json.loads(row[3]), "call": call[0], "call_record": json.loads(call[1]),
              "call_completed_event": call[2], "files": files,
              "association": "Most recent completed call for this counterpart before its sequential driver decision; verify timestamps if using a different driver."}
    name = hashlib.sha256((world+"/"+str(sequence)).encode()).hexdigest()[:16]
    target = audit/"bundles"/f"{number:02d}-decision-{name}.json"
    write_json(target, result)
    return {"bundle": str(target), "actor": row[1], "call": call[0], "event": sequence, "file_count": len(files)}


def watch(cohort, audit, start, until, interval):
    if until is None or not all(math.isfinite(x) for x in (start, until, interval)) or interval < 1 or until < start:
        raise ValueError("watch requires finite start/until, until >= start, and interval >= 1 second")
    number = 0
    while start+number*interval <= until:
        scheduled = start+number*interval
        if time.time()<scheduled:
            time.sleep(min(1, scheduled-time.time())); continue
        r = snapshot(cohort, audit, number, scheduled)
        digest_round(audit, number)
        print(json.dumps({"round": number, "finished": r["finished"], "errors": len(r["errors"])}), flush=True)
        number += 1
    write_json(Path(audit)/"capture-complete.json", {"finished": time.time(), "rounds": number, "meaning": "Evidence capture complete; each round still requires a recorded behavioral evaluation"})


def contract_closure(contract, execution_until):
    """Inspect purchased terms, not a revised offer or a delivery-only verdict."""
    row = {k: contract.get(k) for k in
           ("id", "seller", "owner", "created", "captured", "refunded", "reserved", "renew", "delivered")}
    seconds = contract.get("terms", {}).get("refund_seconds", 0)
    remaining = max(0, contract.get("captured", 0) - contract.get("refunded", 0))
    from .commerce import refund_deadline
    until = refund_deadline(contract) if seconds > 0 else None
    outlives = bool(remaining > 0 and seconds > 0 and (until is None or until > execution_until))
    reasons = []
    if contract.get("reserved", 0): reasons.append("reserved funds remain")
    if contract.get("renew"): reasons.append("renewal remains enabled")
    if remaining > 0 and not contract.get("delivered"): reasons.append("captured payment without recorded delivery")
    if outlives: reasons.append("purchased refund entitlement extends beyond world execution")
    return {**row, "refund_seconds": seconds, "refund_until": until,
            "refund_basis": contract.get("terms", {}).get("refund_basis", "purchase"),
            "unrefunded_captured": remaining,
            "refund_entitlement_outlives_execution": outlives, "review_reasons": reasons}


def closure(cohort, audit):
    """Independent read-only shutdown/obligation inventory, not a business verdict."""
    cohort, audit = Path(cohort).resolve(), Path(audit).resolve()
    target = audit/"closure.json"
    if target.exists(): raise ValueError("refusing to replace closure evidence")
    result = {"at": time.time(), "worlds": {}, "errors": [],
              "limitation": "Verifies local declared scope, not third-party service shutdown or undocumented obligations."}
    m = json.loads((cohort/"cohort.json").read_text())
    result["controller_status"] = m.get("status")
    if m.get("status") != "frozen": result["errors"].append("cohort controller not frozen")
    for name, path in m["worlds"].items():
        root = Path(path).resolve()
        try:
            fork = json.loads((root/"fork.json").read_text()) if (root/"fork.json").exists() else None
            with sqlite3.connect(f"file:{root/'world.sqlite'}?mode=ro", uri=True) as db:
                frozen = json.loads(db.execute("SELECT body FROM meta WHERE key='frozen'").fetchone()[0])
                config = json.loads(db.execute("SELECT body FROM meta WHERE key='config'").fetchone()[0])
                execution_started = fork["started"] if fork else config["started"]
                frozen_at = db.execute("SELECT min(at) FROM events WHERE kind='frozen' AND at>=?", (execution_started,)).fetchone()[0]
                execution_until = min(config["cutoff"], frozen_at) if frozen_at is not None else config["cutoff"]
                pending = db.execute("SELECT count(*) FROM calls WHERE status IN ('reserved','running','uncertain')").fetchone()[0]
                contracts = [{**json.loads(row[2]), "id": row[0], "owner": row[1]} for row in db.execute("SELECT id,owner,body FROM records WHERE kind='contract'")]
            label = "concorde.world="+hashlib.sha256(str(root).encode()).hexdigest()[:12]
            running = subprocess.check_output(["docker", "ps", "--filter", "label="+label, "--format", "{{.Names}}"], text=True, timeout=20).splitlines()
            modes = {p.parent.parent.name: json.loads(p.read_text())["mode"] for p in (root/"subjects").glob("*/.concorde2/state.json")}
            deployment = json.loads((root/"deployment.json").read_text())
            from .replay import deployment_subjects
            stored_subjects = deployment_subjects(deployment)
            inventory = [contract_closure(c, execution_until) for c in contracts]
            result["worlds"][name] = {"frozen": frozen, "pending_calls": pending, "running_containers": running, "modes": modes,
                "deployed_subjects": list(deployment["subjects"]), "inactive_subjects": deployment.get("inactive_subjects", []),
                "execution_started": execution_started, "execution_until": execution_until, "configured_cutoff": config["cutoff"], "frozen_at": frozen_at,
                "contracts": inventory}
            if not frozen or pending or running or set(modes) != set(stored_subjects) or any(v != "frozen" for v in modes.values()):
                result["errors"].append(name+": incomplete local shutdown")
            if any(c["review_reasons"] for c in inventory):
                result["errors"].append(name+": unsettled obligation requires review; not silently canceled")
        except Exception as error:
            result["errors"].append({"world": name, "error": str(error)})
    write_json(target, result)
    return result


if __name__ == "__main__":
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("snapshot", "digest", "compare", "bundle", "decision", "watch", "closure")); p.add_argument("cohort", type=Path); p.add_argument("audit", type=Path)
    p.add_argument("--number", type=int, default=0); p.add_argument("--start", type=float, default=time.time()); p.add_argument("--until", type=float); p.add_argument("--interval", type=int, default=300)
    p.add_argument("--world"); p.add_argument("--actor"); p.add_argument("--activation")
    p.add_argument("--event", type=int)
    a = p.parse_args()
    if a.action == "watch": watch(a.cohort, a.audit, a.start, a.until, a.interval)
    elif a.action == "closure": print(json.dumps(closure(a.cohort, a.audit), indent=2))
    elif a.action == "digest": print(json.dumps(digest_round(a.audit, a.number), indent=2))
    elif a.action == "compare": print(json.dumps(compare_round(a.audit, a.number), indent=2))
    elif a.action == "bundle": print(json.dumps(activation_bundle(a.audit, a.number, a.world, a.actor, a.activation), indent=2))
    elif a.action == "decision": print(json.dumps(decision_bundle(a.audit, a.number, a.world, a.event), indent=2))
    else: print(json.dumps(snapshot(a.cohort, a.audit, a.number), indent=2))

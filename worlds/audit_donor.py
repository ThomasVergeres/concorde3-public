"""Materialize a verified quiescent audit boundary, never modify the live source.

This is an explicitly frozen historical copy, not process or exact time replay.
"""
import hashlib
import json
from pathlib import Path
import sqlite3

from host_runtime import command
from . import forensics, replay
from .engine import World


def read_blob(audit, ref):
    replay.safe_name(ref["path"])
    digest = ref["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or ref["blob"] != "blobs/" + digest or ref.get("truncated"):
        raise ValueError("incomplete or unsafe audit blob")
    path = Path(audit) / ref["blob"]
    replay.regular(path)
    data = path.read_bytes()
    if len(data) != ref["captured_bytes"] or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("audit blob integrity failure")
    return data


def inert_deployment(original):
    return {"image": original["image"], "subjects": {},
            "inactive_subjects": replay.deployment_subjects(original),
            "containers": [], "networks": {}, "status": "frozen", "historical_copy": True}


def restore(audit, number, name, target, binary):
    audit, target = Path(audit).resolve(), Path(target).resolve()
    if target.exists() or target.is_relative_to(audit):
        raise ValueError("fresh independent donor required")
    path = audit / "rounds" / f"{number:02d}.json"
    record = json.loads(path.read_text())
    if record["errors"]:
        raise ValueError("audit capture errors require explicit review")
    entry = record["worlds"][name]
    refs = {r["path"]: r for r in entry["files"]}
    deployment = json.loads(read_blob(audit, refs["deployment.json"]))
    subjects = replay.deployment_subjects(deployment)
    expected = {f"subjects/{actor}/.concorde2/state.json" for actor in subjects}
    if {p for p in refs if p.endswith("/.concorde2/state.json")} != expected:
        raise ValueError("incomplete canonical inventory")
    files, states = {}, {}
    for actor in subjects:
        prefix = f"subjects/{actor}/"
        selected = {p: r for p, r in refs.items() if p.startswith(prefix) and
                    not any(x in replay.EXCLUDED or x.startswith(".env") for x in Path(p).parts)}
        files.update({p: read_blob(audit, r) for p, r in selected.items()})
        state = json.loads(files[prefix + ".concorde2/state.json"])
        if state.get("programs") or any(a.get("status") == "running" or a.get("process", {}).get("pid", 0) for a in state["activations"].values()):
            raise ValueError("historical boundary is not quiescent")
        history = files[prefix + ".concorde2/events.jsonl"]
        events, errors = forensics.events(history)
        if errors or not events or events[-1]["event"]["seq"] != state["seq"] or not forensics.state_matches_replay(state, forensics.replay(history, state["seq"])):
            raise ValueError("exact complete state/journal boundary required")
        states[actor] = {"sequence": state["seq"], "state_sha256": hashlib.sha256(files[prefix + ".concorde2/state.json"]).hexdigest()}
    database = read_blob(audit, entry["database"])
    # Inspect the immutable SQLite blob directly; never open the live source.
    with sqlite3.connect(f"file:{audit / entry['database']['blob']}?mode=ro", uri=True) as db:
        if db.execute("SELECT count(*) FROM calls WHERE status IN ('reserved','running','uncertain')").fetchone()[0]:
            raise ValueError("historical world has unsettled model execution")
        if db.execute("SELECT max(seq) FROM events").fetchone()[0] != entry["event_watermark"]:
            raise ValueError("World watermark differs from audit")
    target.mkdir(parents=True, mode=0o700)
    provenance = {"kind": "audit-quiescent-donor", "audit": str(audit), "round": number, "world": name,
        "round_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "database_sha256": entry["database"]["sha256"],
        "subjects": states, "status": "materializing", "limitations": [
            "Per-world audit boundary, not globally atomic or historical process replay.",
            "Original times, history and working files retained; only this new copy is natively frozen.",
            "Past sessions are evidence, not resumed processes; credentials are not replayed."]}
    forensics.write_json(target / "audit-donor.json", provenance)
    try:
        for name, data in {"world.sqlite": database, **files}.items():
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with dest.open("xb") as out:
                out.write(data)
            dest.chmod(0o600 | (refs.get(name, {}).get("source_mode", 0o600) & 0o100))
        for actor in subjects:
            command([binary, "freeze", target / "subjects" / actor])
        World(target).freeze()
        forensics.write_json(target / "historical-deployment.json", deployment)
        forensics.write_json(target / "deployment.json", inert_deployment(deployment))
        replay.capture(target, target.parent / (target.name + "-snapshot"))
        provenance.update(status="qualified", snapshot=str(target.parent / (target.name + "-snapshot")))
        forensics.write_json(target / "audit-donor.json", provenance)
        return provenance
    except BaseException as error:
        provenance.update(status="failed", error=str(error))
        forensics.write_json(target / "audit-donor.json", provenance)
        raise

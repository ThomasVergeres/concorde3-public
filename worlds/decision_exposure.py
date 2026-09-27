"""Match captured returned World facts to service receipts before an outward act.

This is exposure evidence, never a comprehension or business-success oracle.
Only whole JSON command outputs are supported; unsupported packaging stays
unmatched rather than being guessed from mentions in an assistant's prose.
"""
import hashlib
import json
from pathlib import Path
import sqlite3

from worlds.store import digest


def match(log, exposures, decision):
    completed = []
    errors = []
    for number, line in enumerate(log.splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            errors.append(number)
            continue
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "command_execution":
            continue
        try:
            value = json.loads(item.get("aggregated_output", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            completed.append((number, item.get("id"), value))
    effects = [n for n, _, v in completed if v.get("event") == decision["seq"] and "result" in v]
    if len(effects) != 1:
        return {"status": "unmatched", "reason": "unique returned action receipt absent",
                "incomplete_lines": errors, "matches": []}
    before = effects[0]
    matches = []
    for number, item, value in completed:
        if number >= before or "world" not in value:
            continue
        hashed = digest(value)
        for exposure in exposures:
            if (exposure["actor"] == decision["actor"] and exposure["seq"] < decision["seq"]
                    and exposure["at"] <= decision["at"] and exposure["body"]["result_hash"] == hashed):
                matches.append({"returned_line": number, "item": item, "exposure_event": exposure["seq"],
                                "at": exposure["at"], "result_hash": hashed,
                                "section": exposure["body"]["section"], "world": value["world"]})
    return {"status": "matched" if matches else "unmatched", "matches": matches,
            "decision": decision, "returned_decision_line": before, "incomplete_lines": errors,
            "limitation": "Returned facts before the action, not proof of comprehension or a causal explanation."}


def audit(root, number, world, actor, activation, sequence):
    root = Path(root)
    manifest_path = root / "rounds" / f"{number:02d}.json"
    manifest_raw = manifest_path.read_bytes()
    entry = json.loads(manifest_raw)["worlds"][world]
    def verified(row):
        path = root / row["blob"]
        raw = path.read_bytes()
        if row.get("truncated") or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("incomplete or corrupted exposure source")
        return path, raw
    database, _ = verified(entry["database"])
    name = f"subjects/{actor}/.concorde2/harness-logs/{activation}.work.jsonl"
    source = next(f for f in entry["files"] if f["path"] == name)
    _, raw = verified(source)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT seq,at,actor,kind FROM events WHERE seq=?", (sequence,)).fetchone()
        if row is None or row["actor"] != actor or row["kind"] == "exposure":
            raise ValueError("not this subject's outward action")
        decision = dict(row)
        exposures = [{**dict(r), "body": json.loads(r["body"])} for r in db.execute(
            "SELECT seq,at,actor,body FROM events WHERE kind='exposure' AND actor=? AND seq<?", (actor, sequence))]
    return {"manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "log_sha256": source["sha256"], "database_sha256": entry["database"]["sha256"],
            "world": world, "activation": activation, **match(raw.decode(), exposures, decision)}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audit"); p.add_argument("round", type=int); p.add_argument("world")
    p.add_argument("actor"); p.add_argument("activation"); p.add_argument("sequence", type=int)
    a = p.parse_args()
    print(json.dumps(audit(a.audit, a.round, a.world, a.actor, a.activation, a.sequence), indent=2))

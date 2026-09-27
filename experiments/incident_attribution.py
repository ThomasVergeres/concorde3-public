"""Link an artifact receipt to a captured activation, not a guessed recent author.

Evidence is a returned canonical creation receipt during an activation's interval.
It is not a process checkpoint, proof of comprehension or arbitrary tool replay.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3

from .discrepancy_engine import file_map, read_blob, bounded_text, encoded
from worlds.store import digest


def receipt_lines(log, receipt):
    found = []
    for number, line in enumerate(log.splitlines(), 1):
        try:
            row = json.loads(line); item = row.get("item", {})
            if row.get("type") != "item.completed" or item.get("type") != "command_execution" or item.get("exit_code") != 0: continue
            output = json.loads(item.get("aggregated_output", ""))
            if output == receipt: found.append(number)
        except (ValueError, TypeError, AttributeError):
            continue
    return found


def contains_time(activation, at):
    def epoch(value): return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    try:
        return epoch(activation["started"]) <= at <= epoch(activation["finished"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return False  # Unfinished/undated intervals are not silently fabricated.


def locate(audit, number, world, actor, record_id):
    audit = Path(audit)
    manifest = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    entry = manifest["worlds"][world]; files = file_map(manifest, world)
    state_ref = files[f"subjects/{actor}/.concorde2/state.json"]
    state = json.loads(read_blob(audit, state_ref))
    read_blob(audit, entry["database"])  # Verify complete captured bytes before SQL.
    with sqlite3.connect((audit/entry["database"]["blob"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        matches = []
        for raw, in db.execute("SELECT body FROM receipts WHERE actor=?", (actor,)):
            receipt = json.loads(raw); result = receipt.get("result", {})
            if not isinstance(result, dict) or result.get("id") != record_id or result.get("kind") != "artifact": continue
            event = db.execute("SELECT at,kind,actor FROM events WHERE seq=?", (receipt.get("event"),)).fetchone()
            if event and event[1:] == ("artifact", actor): matches.append((receipt, event[0]))
    if len(matches) != 1:
        return {"status": "unmatched", "reason": "unique canonical artifact-creation receipt absent", "record": record_id}
    receipt, created = matches[0]; witnesses = []; omissions = []
    budget = 64*1024*1024
    for identity, activation in state["activations"].items():
        if not contains_time(activation, created): continue
        prefix = f"subjects/{actor}/.concorde2/harness-logs/{identity}."
        for path, ref in files.items():
            if not path.startswith(prefix) or not path.endswith(".jsonl"): continue
            if ref["captured_bytes"] > budget:
                omissions.append({"activation": identity, "reason": "scan byte bound"}); continue
            budget -= ref["captured_bytes"]
            try: lines = receipt_lines(read_blob(audit, ref).decode(), receipt)
            except (ValueError, UnicodeError) as error:
                omissions.append({"activation": identity, "reason": str(error)}); continue
            if lines: witnesses.append({"activation": identity, "log": path, "sha256": ref["sha256"], "lines": lines})
    ids = sorted({w["activation"] for w in witnesses})
    return {"status": "receipt_observed" if len(ids) == 1 and not omissions else "ambiguous" if ids else "unmatched",
            "world": world, "subject": actor, "record": record_id, "event": receipt["event"],
            "receipt_sha256": digest(receipt), "created": created, "activation_ids": ids,
            "witnesses": witnesses, "omissions": omissions, "state_sha256": state_ref["sha256"],
            "database_sha256": entry["database"]["sha256"],
            "limitation": "Canonical creation receipt returned during a recorded activation, not proof of causal reasoning. Unsupported output packaging or partial logs stay unmatched."}


def receiving_sources(audit, number, world, observation_id):
    """Supply the evaluated object and declared request, not a guessed contract."""
    audit = Path(audit)
    manifest = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    ref = manifest["worlds"][world]["database"]
    read_blob(audit, ref)
    sources, omissions = {}, []
    def add(key, value, limit):
        text, clipped = bounded_text(value, limit)
        sources[key] = {"text": text, "truncated": clipped, "database_sha256": ref["sha256"]}
    with sqlite3.connect((audit/ref["blob"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        def record(identity):
            row = db.execute("SELECT kind,owner,body FROM records WHERE id=?", (identity,)).fetchone()
            return {"id": identity, "kind": row[0], "owner": row[1], "body": json.loads(row[2])} if row else None
        observation = record(observation_id)
        if not observation or observation["kind"] != "observation":
            raise ValueError("captured receiving observation absent")
        add(f"targeted/{world}/receiving/{observation_id}", observation, 90000)
        artifact = record(observation["body"].get("artifact"))
        if artifact and artifact["kind"] == "artifact":
            add(f"targeted/{world}/artifact/{artifact['id']}", artifact, 75000)
            references = artifact["body"].get("source_refs", [])
            for identity in references[:16]:
                source = record(identity)
                if source is None:
                    omissions.append({"source": identity, "reason": "declared source missing"})
                else:
                    add(f"targeted/{world}/declared-source/{identity}", source, 16000)
            if len(references) > 16:
                omissions.append({"reason": "source scan bound", "references": references[16:]})
        else:
            omissions.append({"reason": "evaluated artifact absent from captured records"})
    add(f"targeted/{world}/receiving-boundary/{observation_id}", {
        "observation": observation_id, "omissions": omissions,
        "interpretation": "Receiver tasks/outcomes can be private. Declared source references are not proof that a requirement was served or understood. Compare actual request, evaluated object and receiving contract; a synthetic example must not silently become a promised transformation of undisclosed real inputs. Exact exposure/creation receipts remain separate."}, 6000)
    return sources


def locate_observation(audit, number, world, actor, observation_id):
    """Resolve a receiving result's actual producer before seeking a tool witness."""
    audit = Path(audit)
    manifest = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    ref = manifest["worlds"][world]["database"]
    read_blob(audit, ref)
    with sqlite3.connect((audit/ref["blob"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        row = db.execute("SELECT owner,body FROM records WHERE id=? AND kind='observation'", (observation_id,)).fetchone()
        if row is None:
            return {"status": "unmatched", "observation": observation_id, "reason": "captured observation absent"}
        receiver, body = row[0], json.loads(row[1])
        artifact_id = body.get("artifact")
        artifact = db.execute("SELECT owner,body FROM records WHERE id=? AND kind='artifact'", (artifact_id,)).fetchone()
        producer = json.loads(artifact[1]).get("producer", artifact[0]) if artifact else None
    provenance = {"observation": observation_id, "receiver": receiver, "artifact": artifact_id,
        "artifact_producer": producer, "database_sha256": ref["sha256"]}
    if producer != actor:
        return {**provenance, "status": "non_subject_product" if producer else "unmatched",
            "reason": "receipt attribution is restricted to the actual subject producer"}
    return {**provenance, "creation": locate(audit, number, world, actor, artifact_id)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("audit", type=Path)
    p.add_argument("round", type=int); p.add_argument("world"); p.add_argument("actor"); p.add_argument("record")
    a = p.parse_args(); print(json.dumps(locate(a.audit, a.round, a.world, a.actor, a.record), indent=2))

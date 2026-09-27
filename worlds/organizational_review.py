"""Read-only evidence inventory for organizational-tendency experiments.

No model calls, participant effects, ratings, or inferred private cognition.
Output contains private record references; keep it with experiment evidence.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .attribution import evidence as lineage_evidence


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not value or value.startswith("0001-"):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def assess(records, events, state, actor, started, until):
    """Derive observables, never a systemization/business-success verdict."""
    if until <= started:
        raise ValueError("positive observation window required")
    indexed = {r["id"]: r for r in records}
    events = sorted((e for e in events if started <= e["at"] <= until),
                    key=lambda e: (e["at"], e["seq"]))
    served = {}
    for event in events:
        if event["actor"] != actor:
            continue
        body, kind = event["body"], event["kind"]
        ids = body.get("record_ids", []) if kind == "exposure" else []
        if kind == "inspect":
            ids = [body.get("result", {}).get("id")]
        elif kind == "browse":
            ids = [r.get("id") for r in body.get("result", {}).get("records", [])]
        for identity in ids:
            if identity and identity not in served:
                served[identity] = {"at": event["at"], "event": event["seq"], "via": kind}

    activations, timeline_gaps = [], []
    for activation in (state or {}).get("activations", {}).values():
        begin, end = timestamp(activation.get("started")), timestamp(activation.get("finished"))
        if begin is None:
            timeline_gaps.append(activation.get("id"))
            continue
        if begin > until or (end is not None and end < started):
            continue
        # An unfinished or interrupted activation does not establish an idle
        # interval after its deadline. Retain uncertainty, not invented autonomy.
        activations.append({"id": activation["id"], "intention": activation.get("intention"),
                            "started": begin, "finished": end, "status": activation.get("status"),
                            "recovery_of": activation.get("recovery_of"),
                            "usage": activation.get("usage", {})})

    def overlap(at):
        if state is None or timeline_gaps or any(a["finished"] is None and a["started"] <= at for a in activations):
            return "unknown"
        return "overlap" if any(a["started"] <= at <= a["finished"] for a in activations) else "outside_recorded_activations"

    receiving, uses_by_artifact = [], defaultdict(list)
    for observation in records:
        if observation["kind"] != "observation" or observation["owner"] == actor:
            continue
        at = observation.get("at")
        artifact = indexed.get(observation.get("artifact"), {})
        if at is None or not started <= at <= until or artifact.get("producer", artifact.get("owner")) != actor:
            continue
        row = {"observation": observation["id"], "at": at, "consumer": observation["owner"],
               "project": observation.get("project"), "work_id": observation.get("work_id"),
               "task_hash": observation.get("task_hash") or digest(observation.get("task")),
               "artifact": artifact["id"], "output_hash": digest(observation.get("output")),
               "outcome": observation.get("outcome", {}), "method": observation.get("method"),
               "activation_overlap": overlap(at), "served_to_seller": served.get(observation["id"])}
        receiving.append(row)
        uses_by_artifact[artifact["id"]].append(row)
    receiving.sort(key=lambda r: (r["at"], r["observation"]))
    successful = [r for r in receiving if r["outcome"].get("status") == "passed"]

    messages = [{"message": r["id"], "from": r["owner"], "at": r.get("at"),
                 "served": served.get(r["id"])} for r in records
                if r["kind"] == "message" and r.get("to") == actor
                and started <= r.get("at", -1) <= until]
    midpoint = started + (until - started) / 2
    windows = []
    for label, lo, hi in (("first_half", started, midpoint), ("second_half", midpoint, until)):
        active = [a for a in activations if lo <= a["started"] < hi]
        successes = [r for r in successful if lo <= r["at"] < hi]
        windows.append({"window": label, "starts": len(active),
                        "recovery_starts": sum(bool(a["recovery_of"]) for a in active),
                        "distinct_successful_receiving_work": len({(r["project"], r["work_id"], r["task_hash"]) for r in successes}),
                        "intention_starts": dict(Counter(a["intention"] for a in active))})

    result = {"actor": actor, "window": {"started": started, "until": until},
              "inbound_messages": sorted(messages, key=lambda r: (r["at"], r["message"])),
              "receiving": receiving,
              "receiving_summary": {
                  "attempt_outcomes": dict(Counter(r["outcome"].get("status", "unknown") for r in receiving)),
                  "distinct_successful_work": len({(r["project"], r["work_id"], r["task_hash"]) for r in successful}),
                  "distinct_successful_task_payloads": len({r["task_hash"] for r in successful}),
                  "distinct_consumers": len({r["consumer"] for r in successful}),
                  "reused_immutable_artifacts": [{"artifact": identity, "receiving_attempts": len(rows),
                      "successful_task_payloads": len({r["task_hash"] for r in rows if r["outcome"].get("status") == "passed"})}
                      for identity, rows in sorted(uses_by_artifact.items()) if len(rows) > 1]},
              "activations": sorted(activations, key=lambda a: a["started"]), "windows": windows,
              "state_available": state is not None, "timeline_gaps": timeline_gaps,
              "judgment": "unassessed: inspect actual choices, changed-input outcomes and future attention reuse",
              "limitations": [
                  "Served record evidence establishes tool exposure, not comprehension; absent exposure may be missing instrumentation.",
                  "Receiving counts are direct-producer only; separately reported source lineage, when available, is not additional or causal adoption.",
                  "Passed is the world's narrow receiving contract, not enjoyment, demand, truth, payment or business competence.",
                  "Repeated observations of the same work are retries, not independent customer successes.",
                  "An immutable artifact reused by a buyer is not proof the seller built a reusable system.",
                  "Consumption outside recorded activations is not proof of unattended seller production; a buyer can consume old output.",
                  "Programs, connections and fewer starts are descriptive only; correctness, exceptions, coverage and saved effort require review.",
                  "The halves are descriptive elapsed-time bins, not a causal treatment effect or matched exposure denominator.",
                  "State and world reads are not a distributed atomic snapshot. Missing usage remains unknown.",
              ]}
    if state is not None:
        result["graph"] = {"nodes": len(state.get("nodes", {})), "items": len(state.get("items", {})),
                           "edge_refs": sorted(state.get("edges", {})),
                           "consequence_refs": sorted(state.get("consequences", {}))}
        result["programs"] = [{k: p.get(k) for k in ("id", "intention", "enabled", "status", "last_at")}
                              for _, p in sorted(state.get("programs", {}).items())]
    return result


def review(world, actor, until=None):
    world = Path(world).resolve()
    with sqlite3.connect((world / "world.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        actors = {r[0] for r in db.execute("SELECT id FROM actors")}
        if actor not in actors:
            raise ValueError("unknown actor")
        config = json.loads(db.execute("SELECT body FROM meta WHERE key='config'").fetchone()[0])
        until = min(until if until is not None else time.time(), config["cutoff"])
        records = [{"id": r[0], "kind": r[1], "owner": r[2], **json.loads(r[3])}
                   for r in db.execute("SELECT id,kind,owner,body FROM records")]
        events = [{"seq": r[0], "at": r[1], "actor": r[2], "kind": r[3], "body": json.loads(r[4])}
                  for r in db.execute("SELECT seq,at,actor,kind,body FROM events")]
        lineage = lineage_evidence(db, [actor], limit=32, since=config["started"], until=until)[actor]
    state_path = world / "subjects" / actor / ".concorde2/state.json"
    raw = state_path.read_bytes() if state_path.exists() else None
    result = assess(records, events, json.loads(raw) if raw is not None else None,
                    actor, config["started"], until)
    # Preserve the original direct-use numerator. Source claims are a separate
    # diagnostic, never extra successes or proof of a causal supplier benefit.
    result["source_lineage"] = lineage
    result["provenance"] = {"world": str(world), "event_watermark": max((e["seq"] for e in events), default=0),
                            "state_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                            "world_model": config.get("model"), "world_effort": config.get("effort")}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=Path)
    parser.add_argument("actor")
    args = parser.parse_args()
    print(json.dumps(review(args.world, args.actor), indent=2))

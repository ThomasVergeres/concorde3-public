"""Recover a conservative work-phase usage floor, never declare a tail complete.

Read-only inspection of stopped lab trials. Keeps these counters separate from
the accountant's terminal rectification usage to avoid counting it twice.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from .readiness import baseline, read, units


def seconds(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise ValueError("timezone required")
    return parsed.timestamp()


def phase_floor(events, start, end):
    """Counters must be monotonic within this phase, not summed per tool call."""
    rows = []
    previous = None
    for event in events:
        if not start <= seconds(event["timestamp"]) < end: continue
        payload = event.get("payload", {})
        if payload.get("type") != "token_count": continue
        raw = (payload.get("info") or {}).get("total_token_usage")
        if not raw: continue
        current = {out: raw.get(key) for out, key in (("input", "input_tokens"),
            ("cached", "cached_input_tokens"), ("output", "output_tokens"))}
        if any(type(v) is not int or v < 0 for v in current.values()) or current["cached"] > current["input"]:
            raise ValueError("invalid phase counters")
        if previous and any(current[k] < previous[k] for k in current):
            raise ValueError("counter reset inside selected work phase; manual attribution required")
        previous = current
        rows.append({"at": event["timestamp"], "usage": current})
    if not rows: raise ValueError("no attributable partial work usage")
    return {"first": rows[0], "last": rows[-1], "observations": len(rows)}


def inspect_trial(trial):
    trial = Path(trial).resolve()
    manifest = json.loads((trial / "manifest.json").read_text())
    rt = trial / "subject/.concorde2"
    state = json.loads((rt / "state.json").read_text())
    if state["mode"] != "frozen": raise ValueError("wait for canonical freeze before forensic supplement")
    journal = [json.loads(line)["event"] for line in (rt / "events.jsonl").read_text().splitlines()]
    rows = []
    for key, activation in state["activations"].items():
        if key in manifest.get("inherited_activation_ids", []): continue
        if activation.get("usage", {}).get("quality") != "partial": continue
        if activation["status"] != "completed" or activation.get("config", {}).get("work_reentry"):
            raise ValueError("this helper only qualifies interrupted initial work followed by completed rectification")
        if not activation.get("work_summary", "").startswith("Work interrupted/failed;"):
            raise ValueError("partial usage has a different phase cause")
        log = rt / "harness-logs" / (key + ".work.jsonl")
        work = [json.loads(line) for line in log.read_text().splitlines()]
        if any(e.get("type") == "turn.completed" for e in work):
            raise ValueError("terminal work already belongs in the main accountant")
        if not any(e.get("type") == "thread.started" and e.get("thread_id") == activation["session"] for e in work):
            raise ValueError("work/session identity mismatch")
        boundaries = [e for e in journal if e["actor"] == key and e["kind"] == "activation.rectification"]
        if len(boundaries) != 1: raise ValueError("ambiguous work boundary")
        paths = list((rt / "harness-sessions").rglob("*" + activation["session"] + ".jsonl"))
        if len(paths) != 1 or paths[0].is_symlink(): raise ValueError("ambiguous session file")
        raw = paths[0].read_bytes()
        events = [json.loads(line) for line in raw.splitlines()]
        floor = phase_floor(events, seconds(activation["started"]), seconds(boundaries[0]["at"]))
        usage = {**floor["last"]["usage"], "quality": "measured"}
        weighted = units(usage, read("model-cost-weights.json")["relative_weights"].get(manifest["model"]))
        if weighted is None: raise ValueError("unknown model price weight")
        rows.append({"activation": key, "phase": "interrupted work only", "session": activation["session"],
            "session_sha256": hashlib.sha256(raw).hexdigest(), "work_end_event_seq": boundaries[0]["seq"],
            "work_end": boundaries[0]["at"], "source": str(paths[0]), **floor, "weighted_units": weighted})
    total = sum(r["weighted_units"] for r in rows)
    return {"trial": str(trial), "rows": rows, "additional_weighted_units": total,
        "additional_observed_B": total / baseline(),
        "meaning": "Partial observed floor only. Terminal rectification excluded; original incomplete usage remains unknown. Do not add this supplement twice."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial")
    parser.add_argument("--output", type=Path, help="Fresh versioned supplement; never overwrite earlier accounting")
    args = parser.parse_args()
    result = inspect_trial(args.trial)
    if args.output:
        if args.output.exists(): raise ValueError("fresh supplement output required")
        from .lab import save
        save(args.output, result)
    print(json.dumps(result, indent=2))

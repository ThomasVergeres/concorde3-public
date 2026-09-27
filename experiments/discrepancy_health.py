"""Read-only operational samples. Never infer business competence from liveness."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import time

from evals.lab import save


def rows(path, query):
    with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=3) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(query)]


def sample(root):
    root = Path(root).resolve(); now = time.time()
    manifest = json.loads((root/"cohort.json").read_text())
    result = {"at": now, "status": manifest["status"], "cutoff": manifest.get("cutoff"),
              "worlds": {}, "warnings": [], "meaning": "operational sample, not behavioral evaluation"}
    for name, location in manifest["worlds"].items():
        path = Path(location)
        try:
            calls = rows(path/"world.sqlite", "SELECT at,status,deadline,category,body FROM calls")
            config=json.loads(rows(path/'world.sqlite',"SELECT body FROM meta WHERE key='config'")[0]['body'])
            recent=[c for c in calls if c['at']>now-3600 and c['status']!='undispatched']
            pending = [c for c in calls if c["status"] in ("reserved", "running", "uncertain")]
            overdue = sum(c["deadline"] < now for c in pending)
            states = []
            for state_path in (path/"subjects").glob("*/.concorde2/state.json"):
                state = json.loads(state_path.read_text())
                activations=sorted(state['activations'].values(),key=lambda a:a.get('started',''))
                latest=activations[-1] if activations else {}
                if latest.get('status')=='failed' and any(marker in latest.get('summary','') for marker in
                        ('hourly call cap','all model slots occupied','shared hourly','budget exhausted')):
                    result['warnings'].append({'world':name,'kind':'subject_admission_failure',
                        'activation':latest.get('id'),'phase':latest.get('phase'),
                        'meaning':'Admission denied; inspect resource allocation before behavioral attribution.'})
                states.append({"subject": state_path.parents[1].name, "mode": state["mode"],
                    "activations": dict(Counter(a["status"] for a in state["activations"].values())),
                    "last_finished": max((a.get("finished", "") for a in state["activations"].values()), default=""),
                    "seq": state["seq"]})
            result["worlds"][name] = {"calls": dict(Counter(c["status"] for c in calls)),
                                     "pending": len(pending), "overdue_calls": overdue, "subjects": states,
                                     'local_hourly_budget':{'used':len(recent),'cap':config['calls_per_hour'],
                                         'categories':dict(Counter(c['category'] for c in recent))}}
            if len(recent)>=config['calls_per_hour']*0.9:
                result['warnings'].append({'world':name,'kind':'local_hourly_budget_near_cap',
                    'used':len(recent),'cap':config['calls_per_hour']})
            if overdue: result["warnings"].append({"world": name, "kind": "overdue_call_needs_inspection", "count": overdue})
        except (OSError, ValueError, sqlite3.Error, KeyError) as error:
            result["warnings"].append({"world": name, "kind": "inspection_gap", "error": str(error)[:300]})
    all_reviews = rows(root/"discrepancies.sqlite", "SELECT round,status,kind FROM reviews")
    reviews = [r for r in all_reviews if r["kind"] in ("interval", "terminal")]
    result["curation_reviews"] = dict(Counter(r["status"] for r in all_reviews if r["kind"] == "curation"))
    result["reviews"] = dict(Counter(r["status"] for r in reviews))
    result["last_review_round"] = max((r["round"] for r in reviews), default=0)
    result["jobs"] = rows(root/"discrepancies.sqlite", "SELECT kind,status,count(*) AS count FROM jobs GROUP BY kind,status")
    result["cases"] = rows(root/"discrepancies.sqlite", "SELECT status,count(*) AS count FROM cases GROUP BY status")
    if any(r["status"] in ("failed", "completed_partial", "censored") for r in reviews):
        result["warnings"].append({"kind": "review_gap", "rounds": [r["round"] for r in reviews if r["status"] in ("failed", "completed_partial", "censored")]})
    # A stopped/hung controller may never create a review row. Detect the
    # missing observation against the immutable schedule, without inventing a
    # review, changing its status, or confusing an in-window call with a gap.
    schedule = rows(root/"discrepancies.sqlite", "SELECT number,scheduled FROM rounds WHERE number>0")
    observed = {r["round"] for r in reviews if r["status"] != "running"}
    missed = sorted(r["number"] for r in schedule if r["number"] not in observed
                    and now >= r["scheduled"] + manifest.get("interval_seconds", 600))
    if missed:
        result["warnings"].append({"kind": "review_window_missed", "rounds": missed})
    return result


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path); p.add_argument("--output", type=Path)
    p.add_argument("--until", type=float); args = p.parse_args()
    while True:
        try: value = sample(args.root)
        except (OSError, ValueError, sqlite3.Error) as error:
            value = {"at": time.time(), "status": "inspection_gap", "error": str(error)[:300]}
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (args.output/"samples.jsonl").open("a") as stream:
                stream.write(json.dumps(value)+"\n"); stream.flush(); os.fsync(stream.fileno())
            save(args.output/"latest.json", value)
        print(json.dumps(value), flush=True)
        if args.until is None or time.time() >= args.until: break
        time.sleep(max(0, min(30, args.until-time.time())))


if __name__ == "__main__": main()

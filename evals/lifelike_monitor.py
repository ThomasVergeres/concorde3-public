"""Read-only campaign observations and private legacy-session capture.

Used for the current campaign's pre-session-bind trials. Does not start, wake,
repair, grade or stop a company. New trials use persistent private session binds.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import time

from .lab import save

CAPTURE = """import base64,json,pathlib,os,stat
root=pathlib.Path('/home/node/.codex/sessions'); rows=[]; total=0; omitted=0
for p in sorted(root.rglob('*.jsonl')):
 if p.is_symlink() or any(q.is_symlink() for q in p.parents) or 'auth' in p.name.lower(): continue
 if not p.resolve().is_relative_to(root.resolve()): continue
 if len(rows)>=256 or total>=67108864: omitted+=1; continue
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  st=os.fstat(f.fileno())
  if not stat.S_ISREG(st.st_mode): continue
  data=f.read(min(16777216,67108864-total))
 total+=len(data)
 rows.append({'path':str(p.relative_to(root)),'source_bytes':st.st_size,'truncated':len(data)<st.st_size,'data':base64.b64encode(data).decode()})
print(json.dumps({'sessions':rows,'captured_bytes':total,'omitted':omitted}))
"""


def snapshot(root, capture=False, panels=()):
    root = Path(root).resolve()
    at = dt.datetime.now(dt.timezone.utc).isoformat()
    records = []
    paths=[p for p in sorted(root.glob("*/*/manifest.json")) if not panels or p.parent.parent.name in panels]
    if panels and set(panels)-{p.parent.parent.name for p in paths}:
        raise ValueError("requested panel missing; absence is not proof of stopped execution")
    for path in paths:
        manifest = json.loads(path.read_text())
        state_path = path.parent/"subject/.concorde2/state.json"
        if not state_path.exists(): continue
        state = json.loads(state_path.read_text())
        container = "c3-lab-"+manifest["id"]
        probe = subprocess.run(["docker", "inspect", container, "--format", "{{.State.Running}}"], capture_output=True, text=True, timeout=15)
        answer=probe.stdout.strip()
        running = answer == "true" if probe.returncode == 0 and answer in ("true","false") else None
        inherited=set(manifest.get("inherited_activation_ids",[]))
        record = {"panel":path.parent.parent.name, **{k:manifest[k] for k in ("id","case","variant","world_seed","model")},
            "arm":manifest.get("arm"),
            "mode":state["mode"], "container_running":running,
            "container_inspection_error":None if running is not None else (probe.stderr[-500:] or "unrecognized container state"),
            "inherited_activation_count":len(inherited & set(state["activations"])),
            "programs":{key:{k:p.get(k) for k in ("status","enabled","process","last_error")} for key,p in state.get("programs",{}).items()},
            "activations":[{k:a.get(k) for k in ("id","status","phase","started","finished","summary","work_summary","recovery_of","completion","usage")}
                for key,a in state["activations"].items() if key not in inherited and a.get("usage",{}).get("basis") != "constructed"]}
        for name in ("systemization-observation.json", "lifelike-observation.json", "result.json"):
            p=path.parent/name
            if p.exists(): record[name]=json.loads(p.read_text())
        if capture and running and not (path.parent/"subject/.concorde2/harness-sessions").exists():
            target=path.parent/"legacy-session-captures"/str(time.time_ns())
            target.mkdir(parents=True, mode=0o700)
            # Docker archive-copy does not expose these live tmpfs mounts.
            # Bounded structured data, never tar extraction or subject code.
            copied=subprocess.run(["docker","exec",container,"python3","-c",CAPTURE], capture_output=True, text=True, timeout=45)
            if copied.returncode == 0: save(target/"capture.json",json.loads(copied.stdout))
            record["legacy_session_capture"]={"path":str(target.relative_to(root)),"returncode":copied.returncode,
                "error":copied.stderr[-500:] if copied.returncode else None,
                "limit":"live session tails may be incomplete; encrypted reasoning remains opaque"}
        records.append(record)
    result={"at":at,"basis":"observed state/effects; not an automatic competence verdict","trials":records}
    save(root/"monitor"/(str(time.time_ns())+".json"),result)
    return result


def activation_counts(activations):
    return {
        "completed":sum(a["status"]=="completed" for a in activations),
        "failed":sum(a["status"]=="failed" for a in activations),
        "in_flight":sum(a["status"]=="running" for a in activations),
        "rectification_pending":sum(a.get("phase")=="rectification_pending" for a in activations),
        # Recovery carries the original interrupted work summary; it is not
        # another failed work turn. Its own failure still counts under failed.
        "reported_work_interruptions":sum(not a.get("recovery_of") and
            (a.get("work_summary") or "").startswith("Work interrupted/failed;") for a in activations)}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("--capture",action="store_true")
    parser.add_argument("--panel",action="append",default=[],help="Inspect only these named panels; fail if a requested panel is missing")
    args=parser.parse_args()
    r=snapshot(args.root,args.capture,args.panel)
    print(json.dumps({"at":r["at"],"trials":[{k:v for k,v in t.items() if k not in ("systemization-observation.json","lifelike-observation.json","result.json","activations")}|
        activation_counts(t["activations"]) for t in r["trials"]]},indent=2))

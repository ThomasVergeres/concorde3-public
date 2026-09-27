"""Read-only regrading into a new revisioned file; originals remain untouched."""
import argparse
import json
import re
from pathlib import Path
from .grade import REVISION, outcome, markdown
from .lab import read_json, save

def main():
    p=argparse.ArgumentParser();p.add_argument("run");p.add_argument("--tag",default="");args=p.parse_args()
    if args.tag and not re.fullmatch(r"[a-zA-Z0-9_-]+",args.tag):p.error("invalid report tag")
    root=Path(args.run).resolve()
    results=[]
    for path in sorted(root.glob("*/result.json")):
        row=read_json(path,root)
        obs=read_json(path.parent/"observation.json",root)
        # Older collectors did not copy this existing driver artifact. Recover
        # transport provenance without rewriting the original observation or
        # pretending an interrupted activation supplied complete usage metrics.
        preflight=path.parent/"subject/.concorde2/lab-start.json"
        if "subscription_preflight" not in obs["telemetry"] and preflight.exists():
            obs["telemetry"]["subscription_preflight"]=read_json(preflight,root).get("auth_basis")=="subscription"
            row["supplemental_auth_provenance"]=str(preflight)
        facts=read_json(path.parent/"facts.json",root)
        row["result"]=outcome(facts,obs["state"],obs["artifacts"],obs["telemetry"])
        errors=obs["telemetry"].get("errors",[])
        capture_only=[e for e in errors if "'docker', 'cp'" in e and "/home/node/.codex/sessions" in e]
        if capture_only:
            row["recording_limitations"] = capture_only
        if len(errors)>len(capture_only):
            row["result"]["label"]="runtime_failure"
        row["regraded_from"]=str(path)
        results.append(row)
    out=root/("regraded-v"+REVISION+("-"+args.tag if args.tag else "")+".json")
    if out.exists(): raise RuntimeError("regrade revision already exists; do not rewrite evidence")
    save(out,results)
    out.with_suffix(".md").write_text(markdown(results))
    print(out)

if __name__=="__main__":main()

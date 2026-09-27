"""Qualify new graders against captured real failures; does NOT rerun a model."""
import argparse
import hashlib
import json
from pathlib import Path
from .grade import outcome
from .lab import read_json, save

INCIDENTS = (
    ("source", "c3-runway3-heldout-20260910/bc9778e8b9b8", "AR05", "challenge"),
    ("conditional-release-engineering", "c3-runway3-tail-20260910/2a2098f021a8", "AR06", "challenge"),
    ("conditional-release-research", "c3-runway3-tail-20260910/62cc44353e7f", "AR06", "challenge"),
)

def replay(root):
    results=[]
    for category,relative,family,variant in INCIDENTS:
        path=Path(root)/relative
        facts=read_json(path/"facts.json")
        evidence=read_json(path/"observation.json")
        # Old AR04 'control' means release after response, not immediate
        # cancellation: it is precisely AR06's conditional-release challenge.
        mapped={**facts,"family":family,"variant":variant}
        result=outcome(mapped,evidence["state"],evidence["artifacts"],evidence["telemetry"])
        results.append({"category":category,"source":str(path),"result":result,
                        "source_hashes":{name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in ("facts.json","observation.json","manifest.json")},
                        "basis":"New endpoint applied to a previously captured actual model failure. No fresh model draw, repaired response or simulated success."})
    return results

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise ValueError("preserve previous replay evidence")
    rows=replay(a.root);save(a.output,rows)
    print(json.dumps([{ "category":r["category"],"label":r["result"]["label"]} for r in rows]))
    raise SystemExit(0 if all(r["result"]["label"]=="behavioral_failure" for r in rows) else 1)

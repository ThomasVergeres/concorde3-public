"""Finish only undispatched cells after a setup-interrupted trajectory panel.

Operator utility, not a Concorde capability. No retry of dispatched behavior and
no semantic verdict. Run from a snapshotted evals package for unattended use.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from .lab import cell_key, dispatched_cells, read_json, save


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("root",type=Path)
    p.add_argument("--wait-pid",type=int,required=True)
    p.add_argument("--until",type=float,required=True)
    p.add_argument("--auth",required=True)
    args=p.parse_args()
    root=args.root.resolve();original=root/"short-loop"
    while time.time()<args.until:
        try:cmdline=Path(f"/proc/{args.wait_pid}/cmdline").read_bytes().split(b"\0")
        except FileNotFoundError:break
        if str(original).encode() not in cmdline or b"evals.lab" not in cmdline:break
        time.sleep(1)
    else:raise RuntimeError("Original runner still active at declared continuation cutoff; no duplicate dispatch")
    plan=read_json(original/"plan.json",root)
    excluded=dispatched_cells(original)
    remaining=[c for c in plan["cells"] if cell_key(c) not in excluded]
    status={"at":time.time(),"excluded_dispatched":len(excluded),"remaining":len(remaining),"basis":"Infrastructure completion only; no behavioral retries"}
    save(root/"panel-continuation.json",status)
    if remaining:
        images={arm:{c["image"] for c in plan["cells"] if c["arm"]==arm} for arm in ("baseline","candidate")}
        if any(len(v)!=1 for v in images.values()):raise RuntimeError("This continuation requires one pinned image per arm")
        limits=plan["limits"]
        if time.time()+limits["wall"]+90>args.until:raise RuntimeError("Insufficient declared window for another episode")
        cmd=[sys.executable,"-m","evals.lab","run","--output",str(root/"short-loop-remaining"),"--exclude-dispatched-from",str(original),
            "--auth",args.auth,"--image",next(iter(images["baseline"])),"--candidate-image",next(iter(images["candidate"])),
            "--fixture",str(original/"assets/lab-fixture"),"--candidate-fixture",str(original/"assets/candidate-fixture"),
            "--cases",",".join(sorted({c["case"] for c in plan["cells"]})),"--profiles","situated","--variants","challenge,control",
            "--worlds","1","--models","terra-medium","--draws","1","--workers","8","--entry","episode",
            "--starts",str(limits["starts"]),"--deadline",str(limits["deadline"]),"--wall",str(limits["wall"])]
        try:
            subprocess.run(cmd,check=True,timeout=max(1,args.until-time.time()))
        except subprocess.TimeoutExpired:
            # A runner timeout must not leave its last dispatched embodiments
            # alive until their later per-episode watchdogs. Exact manifest IDs
            # define the only allowed shutdown scope.
            for path in (root/"short-loop-remaining").glob("*/manifest.json"):
                identity=read_json(path,root)["id"]
                if len(identity)!=12 or any(c not in "0123456789abcdef" for c in identity):
                    raise RuntimeError("Invalid experiment-owned container ID")
                name="c3-lab-"+identity
                subprocess.run(["docker","exec",name,"concorde3","freeze","/instance"],capture_output=True,timeout=20)
                subprocess.run(["docker","stop","-t","3",name,name+"-transport"],capture_output=True,timeout=20)
            raise
    for directory in (original,root/"short-loop-remaining",root/"integrity-regressions-v2",root/"feedback-regressions"):
        if directory.exists():
            subprocess.run([sys.executable,"-m","evals.audit",str(directory),"--tag","final-panel"],check=True)
    actual=set()
    for directory in (original,root/"short-loop-remaining"):
        for path in directory.glob("*/result.json"):
            result=read_json(path,root)
            if result.get("telemetry",{}).get("dispatched"):actual.add(cell_key(result))
    expected={cell_key(c) for c in plan["cells"]}
    status.update(finished=time.time(),reported_dispatched=len(actual),missing=sorted(expected-actual),
        semantic_review="Still required; programmatic completion does not establish competence")
    save(root/"panel-complete.json",status)
    if expected-actual:raise RuntimeError("Missing dispatched result records; panel remains incomplete")


if __name__=="__main__":main()

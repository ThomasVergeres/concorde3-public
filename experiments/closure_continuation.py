"""Finite unchanged-C3 closure comparison using existing World launch/capture.

One Luna draw per condition; ordinary counterpart messages, no private wake.
Run only after fixture qualification. This runner never interprets output as
business success or changes practices to improve an in-progress outcome.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from experiments.reporting_dependency import accounting, command, REPO, SUPPLEMENTS
from worlds import campaign, closure_probe, forensics, replay
from worlds.engine import World
from worlds.forensics import write_json

PRIOR_PANEL = ["reporting-dependency/baseline-01/"+v for v in ("retiring", "adequate", "self-contained")]


def account(root, additions=()):
    return accounting(root, [root/p for p in PRIOR_PANEL]+list(additions))


def plan(root):
    observed = account(root)
    partial = sum(json.loads((root/p).read_text())["additional_observed_B"] for p in SUPPLEMENTS)
    total = observed["known_multiple"] + partial + .50 + .05
    if observed["accounting_errors"] or observed["shot_violations"] or total > 3.15:
        raise ValueError("shared campaign accounting/reservation gate failed")
    return observed, {"approved_ceiling_B":3.15,"unknown_allowance_B":.50,"panel_reserve_B":.05,
                      "partial_supplement_B":partial,"planning_B":total}


def run(root, output, snapshot, fixture, binary, image, invitation):
    root, output, snapshot = (Path(p).resolve() for p in (root,output,snapshot))
    if output.exists():raise ValueError("fresh panel required; no implicit repeat")
    if command(["git","status","--porcelain"]):raise ValueError("commit runner/protocol before dispatch")
    if command(["docker","ps","-q"]):raise ValueError("unexpected active containers")
    observed, reservation = plan(root)
    source = replay.load(snapshot); original = Path(source["source"])
    before = {r["path"]:hashlib.sha256((original/r["path"]).read_bytes()).hexdigest() for r in source["files"]}
    if any(before[r["path"]]!=r["sha256"] for r in source["files"]):raise ValueError("source changed")
    image=command(["docker","image","inspect",image,"--format","{{.Id}}"])
    output.mkdir(mode=0o700,parents=True)
    meta={"status":"preparing","started":time.time(),"controller_pid":os.getpid(),"worlds":{},"processes":[],"errors":[],
          "source_revision":command(["git","rev-parse","HEAD"]),"image":image,
          "source_manifest":hashlib.sha256((snapshot/"manifest.json").read_bytes()).hexdigest(),
          "runner_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "model":"gpt-5.6-luna","effort":"xhigh","draw":0,"reservation":reservation,
          "meaning":"One lived origin, prospective inquiry not accepted duty; no automatic business verdict.",
          "subscription":"company_subject preflight required; no API fallback"}
    write_json(output/"pre-account.json",observed);write_json(output/"cohort.json",meta)
    children,logs=[],[]
    try:
        for variant in closure_probe.VARIANTS:
            location=output/variant
            meta["worlds"][variant]=str(location);write_json(output/"cohort.json",meta)
            f=replay.fork(snapshot,location,fixture,hours=52/60,model="gpt-5.6-luna",effort="xhigh",
                          starts=3,acknowledge_dormancy=True)
            world=World(location)
            for actor in source["subjects"]:
                if actor!="reach":command([binary,"freeze",location/"subjects"/actor])
            with world.s.transaction() as db:
                cfg=world.s.meta(db,"config");cfg["counterpart_mode"]="scripted"
                world.s.meta(db,"config",cfg)
            command([binary,"configure",location/"subjects/reach",json.dumps({
                "freeze_at":dt.datetime.fromtimestamp(f["started"]+3000,dt.UTC).isoformat(),"deadline_seconds":300})])
            record=closure_probe.prepare(world,invitation,variant)
            if record["seller"]!="reach":raise ValueError("unexpected actual invitation owner")
            prefix="c3-world-"+campaign.world_id(location)
            at=dt.datetime.fromtimestamp(f["started"]+3003,dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            command(["sudo","-n","systemd-run","--quiet","--unit",prefix+"-subject-stop","--on-calendar",at,
                     "--timer-property=AccuracySec=1s","--property=User=codex","/usr/bin/docker","stop","-t","3",prefix+"-reach"])
        for variant,location in meta["worlds"].items():
            for role,args in (("observer",[sys.executable,"-m","worlds.closure_probe",location]),
                              ("runtime",[sys.executable,"-m","worlds.cli","run",location,"--image",image,"--subjects","reach"])):
                log=(output/(variant+"-"+role+".log")).open("x");logs.append(log)
                child=subprocess.Popen(args,cwd=REPO,stdout=log,stderr=subprocess.STDOUT)
                children.append(child);meta["processes"].append({"variant":variant,"role":role,"pid":child.pid})
        meta.update(status="running",dispatched_at=time.time());write_json(output/"cohort.json",meta)
        print(json.dumps({"running":meta["processes"],"reservation":reservation}),flush=True)
        first=min(json.loads((Path(p)/"fork.json").read_text())["started"] for p in meta["worlds"].values())
        offsets=[20,300,590,610,900,1800,2400,2710,3010,3130];number=0
        while any(p.poll() is None for p in children) or number<len(offsets):
            if any(p.poll() not in (None,0) for p in children):raise RuntimeError("child failed; stop incomplete comparison, no hidden retry")
            if number<len(offsets) and time.time()>=first+offsets[number]:
                capture=forensics.snapshot(output,output/"audit",number,scheduled=first+offsets[number])
                forensics.digest_round(output/"audit",number)
                write_json(output/f"counterparts-round-{number:02d}.json",{
                    v:json.loads((Path(p)/closure_probe.FILE).read_text()) for v,p in meta["worlds"].items()})
                print(json.dumps({"round":number,"at":time.time(),"errors":capture["errors"]}),flush=True)
                number+=1
            if time.time()>first+3210:raise RuntimeError("finite closure grace exceeded")
            time.sleep(1)
    except BaseException as error:
        meta["errors"].append({"error":str(error),"stderr":getattr(error,"stderr",None)});raise
    finally:
        for variant,p in meta["worlds"].items():
            try:campaign.freeze(World(Path(p)))
            except Exception as error:meta["errors"].append({"freeze":variant,"error":str(error)})
            for actor in source["subjects"]:
                try:command([binary,"freeze",Path(p)/"subjects"/actor])
                except Exception as error:meta["errors"].append({"freeze":variant+"/"+actor,"error":str(error)})
        for child in children:
            if child.poll() is None:child.terminate()
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:meta["errors"].append({"pid":child.pid,"error":"still live after SIGTERM"})
        for log in logs:log.close()
        meta.update(status="frozen",finished=time.time(),exit_codes=[c.poll() for c in children],
                    source_unchanged=all(hashlib.sha256((original/p).read_bytes()).hexdigest()==h for p,h in before.items()))
        write_json(output/"cohort.json",meta)
        write_json(output/"final-account.json",account(root,[Path(p) for p in meta["worlds"].values()]))
        print(json.dumps({"finished":meta["finished"],"errors":meta["errors"],"exit_codes":meta["exit_codes"]}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ("root","output","snapshot","fixture","binary","image","invitation"):parser.add_argument("--"+key,required=True)
    run(**vars(parser.parse_args()))

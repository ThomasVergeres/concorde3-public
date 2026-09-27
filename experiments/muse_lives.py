"""Three fresh, isolated Muse companies; finite two-hour owner-requested panel."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from worlds.engine import World
from worlds import campaign, forensics
from evals.lab import command, save


def configure(world, endpoint, token_file, adapter):
    with world.s.transaction() as db:
        cfg = world.s.meta(db, "config")
        cfg.update(model="muse-spark-1.3-contributor", effort="high",
            api_harness={"endpoint":endpoint,"token_file":str(Path(token_file).resolve()),"adapter":str(Path(adapter).resolve())},
            baseline_starts=20, maximum_starts=20, activation_deadline=300,
            counterpart_calls_per_hour=4, counterpart_response_reserve=1,
            shared_concurrency=8, calls_per_hour=200, concurrency=4)
        world.s.meta(db,"config",cfg)
    return cfg


def run(args):
    root=Path(args.output).resolve()
    if root.exists():raise ValueError("fresh output required, no implicit relaunch")
    image=command(["docker","image","inspect",args.image,"--format","{{.Id}}"])
    root.mkdir(parents=True,mode=0o700)
    os.environ["WORLD_BUDGET_FILE"]=str(root/"world-budget/calls.jsonl")
    meta={"status":"preparing","controller_pid":os.getpid(),"started":time.time(),"worlds":{},"processes":[],"errors":[],
          "model":"muse-spark-1.3-contributor","effort":"high","image":image,"source_revision":command(["git","rev-parse","HEAD"]),
          "limits":{"hours":2,"starts_per_hour_per_subject":20,"concurrency_per_subject":1,"counterpart_calls_per_hour":4},
          "meaning":"Fresh simulated businesses with model-driven counterparts; no external customers, revenue or production-readiness claim."}
    processes=[];logs=[]
    try:
        for pack,seed,subjects in (("market",131,["reach","steward"]),("consumer",137,["everyday"])):
            path=root/pack;world=World(path);world.create(pack,seed,hours=2)
            cfg=configure(world,args.api_broker,args.api_broker_token,args.api_adapter)
            meta["worlds"][pack]=str(path)
            save(root/"cohort.json",meta)
            log=(root/(pack+".log")).open("x");logs.append(log)
            env={**os.environ,"WORLD_BUDGET_FILE":str(root/"world-budget/calls.jsonl")}
            proc=subprocess.Popen([sys.executable,"-m","worlds.cli","run",str(path),"--image",image,"--subjects",*subjects],stdout=log,stderr=subprocess.STDOUT,env=env)
            processes.append(proc)
            meta["processes"].append({"world":pack,"pid":proc.pid,"cutoff":cfg["cutoff"]})
        meta["status"]="running";save(root/"cohort.json",meta)
        print(json.dumps({"running":meta["processes"],"subjects":["reach","steward","everyday"]}),flush=True)
        number=0;next_at=time.time()+45
        end=max(p["cutoff"] for p in meta["processes"])
        while time.time()<end+60 and any(p.poll() is None for p in processes):
            if any(p.poll() not in (None,0) for p in processes):raise RuntimeError("World controller failed; retain result, no restart")
            if time.time()>=next_at:
                snap=forensics.snapshot(root,root/"audit",number,scheduled=next_at)
                digest=forensics.digest_round(root/"audit",number)
                save(root/f"review-{number:02d}.json",{"at":time.time(),"capture_errors":snap["errors"],"digest":digest,
                    "limitation":"Periodic receipts/state summary, not an automatic business-quality verdict."})
                print(json.dumps({"round":number,"capture_errors":snap["errors"]}),flush=True)
                number+=1;next_at=meta["started"]+number*600
            time.sleep(2)
    except BaseException as error:
        meta["errors"].append({"error":str(error)})
        raise
    finally:
        for path in meta["worlds"].values():
            try:campaign.freeze(World(Path(path)))
            except Exception as error:meta["errors"].append({"freeze":path,"error":str(error)})
        for proc in processes:
            if proc.poll() is None:proc.terminate()
            try:proc.wait(timeout=30)
            except subprocess.TimeoutExpired:meta["errors"].append({"live_controller":proc.pid})
        for log in logs:log.close()
        meta.update(status="closed_unverified",finished=time.time(),exit_codes=[p.poll() for p in processes])
        save(root/"cohort.json",meta)
        print(json.dumps({"closed":meta["finished"],"errors":meta["errors"]}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ("output","image","api-broker","api-broker-token","api-adapter"):parser.add_argument("--"+key,required=True)
    run(parser.parse_args())

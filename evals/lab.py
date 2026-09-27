"""Run isolated, bounded C3 episodes. Python standard library + Docker + systemd.

Usage: python3 -m evals.lab run --help; python3 -m unittest discover -s evals -t .
Evaluator files stay on the host; only neutral execution inputs enter subjects.
"""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid

from .cases import materialize, IMPLEMENTED, stamp
from .grade import outcome, markdown, export_correct, product_correct
from .model_transport import MUSE_PROFILE, MUSE_MODEL
from host_runtime import command, save, create_network as allocate_network, prepare_session_storage

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROFILES = {"luna-xhigh":("gpt-5.6-luna","xhigh"),"terra-low":("gpt-5.6-terra","low"),"terra-medium":("gpt-5.6-terra","medium"),"luna-high":("gpt-5.6-luna","high")}
PROFILES.update({'luna6-xhigh':('gpt-6-luna','xhigh'),'sol6-medium':('gpt-6-sol','medium')})
PROFILES['luna6-max'] = ('gpt-6-luna', 'max')
PROFILES[MUSE_PROFILE] = (MUSE_MODEL, "high")
HOST_CALL_CEILING = 5000  # Owner authorization; invocations conservatively bound activations.
EPISODE_SLOTS = 16
MAINTAINED = ("BD01","AT01","AT02","AT03","FC01","HD01","AU02","BP01","PC01","UX01","AR01","AR02","AR03","AR04","AR05","SY01","RC01","BP02","UX02","SY02","SY03","SY04","SY05","SY06","AC01","GC01")

MAINTAINED += ("AR09",)

def execution_limits(spec, entry, starts, family):
    inherited=bool(spec.get("rectification_summary") or spec.get("settled_checkpoint"))
    one=entry=="phase_probe" or (starts==1 and family not in MAINTAINED)
    # A bounded multi-start episode must not rely on the controller winning a
    # polling race against a spare third admission. Single probes deliberately
    # retain spare capacity so their phase boundary is not budget exhaustion.
    floor = 3 if one or family in MAINTAINED else 1
    ceiling=max(starts+int(inherited)+spec.get("recent_starts",0),floor)
    live=1 if one else ceiling-int(inherited)-spec.get("recent_starts",0)
    return inherited,ceiling,live

def invocation_reservation(config, live_starts):
    return live_starts * (4 if config.get('work_reentry') else 2)

def comparison_arms(args):
    if args.single_arm == "candidate":
        return [("candidate",args.candidate_image or args.image,args.candidate_kit)]
    arms=[(args.single_arm,args.image,None)]
    if args.aa or args.candidate_image or args.candidate_kit or args.candidate_fixture or json.loads(args.candidate_config):
        arms.append(("candidate",args.candidate_image or args.image,args.candidate_kit))
    return arms

def session_mount(workspace):
    # Share the already-qualified private-session storage contract with worlds;
    # auth remains a separate read-only mount under an ephemeral Codex home.
    return prepare_session_storage(workspace), "/home/node/.codex/sessions", False

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def image_practices(image):
    """Seed from the tested image, not the host fixture's embedded practices."""
    script="import tempfile,subprocess,json,pathlib; p=tempfile.mkdtemp(); subprocess.run(['concorde3','init','--goal','Seed provenance',p],check=True,stdout=subprocess.DEVNULL); s=json.loads((pathlib.Path(p)/'.concorde2/state.json').read_text()); print(json.dumps([s['items'][k] for k in ('memory-practice','rectification-practice')]))"
    items=json.loads(command(["docker","run","--rm","--network","none","--entrypoint","python3",image,"-c",script]))
    return [{k:i[k] for k in ("id","node","kind","text","status")} for i in items]

def read_json(path, root=None, max_bytes=16_000_000):
    path = Path(path)
    if root and not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("subject reference escapes evidence root")
    fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        data = f.read(max_bytes+1)
    if len(data)>max_bytes: raise ValueError("evidence exceeds read bound")
    return json.loads(data)

def container_running(name):
    p=subprocess.run(["docker","inspect",name,"--format","{{.State.Running}}"],capture_output=True,text=True,timeout=15)
    if p.returncode:
        if "no such object:" in p.stderr.lower() or "no such container:" in p.stderr.lower():return False
        raise RuntimeError("cannot establish previous slot container state")
    return p.stdout.strip()=="true"

class Ledger:
    """Dispatched reservations never refunded; provably undispatched ones may void."""
    def __init__(self,path,cap=200): self.path,self.cap=Path(path),cap
    def reserve(self, run_id, invocations, other_starts):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open("a+") as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            f.seek(0)
            rows=[json.loads(s) for s in f if s.strip()]
            voided={r["void"] for r in rows if "void" in r}
            used=sum(r["invocations"] for r in rows if "invocations" in r and r["id"] not in voided and r["at"]>time.time()-3600)
            if used+invocations>self.cap or used+invocations+other_starts>HOST_CALL_CEILING:
                raise RuntimeError("lab/host hourly reservation exhausted; queue next run after refill")
            f.seek(0,2)
            f.write(json.dumps({"at":time.time(),"id":run_id,"invocations":invocations,"other_start_ceiling":other_starts})+"\n")
            f.flush();os.fsync(f.fileno())

    def void_undispatched(self, run_id, evidence):
        if not evidence: raise ValueError("absence of dispatch needs evidence")
        with self.path.open("a+") as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            f.write(json.dumps({"at":time.time(),"void":run_id,"reason":evidence})+"\n")
            f.flush();os.fsync(f.fileno())

    @contextlib.contextmanager
    def slot(self):
        """Shared bounded episode slots, not a separate pool per runner."""
        directory=self.path.parent/"model-slots"
        directory.mkdir(parents=True,exist_ok=True)
        held=None
        while held is None:
            for index in range(EPISODE_SLOTS):
                candidate=(directory/str(index)).open("a+")
                try:fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:candidate.close();continue
                # A dead runner releases flock before its independent watchdog
                # necessarily stops the container. Keep that slot occupied until
                # actual termination is verified, rather than minting capacity.
                candidate.seek(0)
                previous=candidate.read()
                try:
                    if previous and container_running(json.loads(previous)["container"]):
                        candidate.close();continue
                except Exception:
                    candidate.close();raise
                held=candidate;break
            if held is None:time.sleep(.25)
        try:yield held
        finally:held.close()

def create_network(name, *, isolate_host=False):
    return allocate_network(name, isolate_host=isolate_host, run=command)

def other_capacity():
    """Refuse unknown live C3 configurations; don't infer idle means zero ceiling."""
    names=command(["docker","ps","--format","{{.Names}}"]).splitlines()
    total=0
    inspections={}
    worlds={}
    for name in names:
        if name.startswith("c3-lab-") or not (name.startswith("c3-") or "concorde" in name.lower()):continue
        inspection=json.loads(command(["docker","inspect",name]))[0]
        inspections[name]=inspection
        label=inspection.get("Config",{}).get("Labels",{}).get("concorde.world")
        mounts={m.get("Destination"):m.get("Source") for m in inspection.get("Mounts",[])}
        if label and "/data" in mounts and "worlds.cli" in inspection.get("Args",[]):
            root=Path(mounts["/data"]).resolve()
            if hashlib.sha256(str(root).encode()).hexdigest()[:12]!=label:
                raise RuntimeError("world gateway provenance mismatch")
            with sqlite3.connect(f"file:{root/'world.sqlite'}?mode=ro",uri=True) as db:
                row=db.execute("SELECT body FROM meta WHERE key='config'").fetchone()
                cfg=json.loads(row[0])
            worlds[label]=int(cfg["calls_per_hour"])
    total+=sum(worlds.values()) # includes actual counterpart calls, not zero
    for name in names:
        if name.startswith("c3-lab-"): continue # Shared reservation ledger covers these.
        if not (name.startswith("c3-") or "concorde" in name.lower()): continue
        # Verified world/company sidecars have no instance mount and cannot
        # contain a C3 scheduler. Do not mistake their names for unknown selves.
        inspection=inspections[name]
        if not any(m.get("Destination")=="/instance" for m in inspection.get("Mounts",[])):
            label=inspection.get("Config",{}).get("Labels",{}).get("concorde.world")
            if label in worlds:continue # provenance-checked, cap accounted above
            config=inspection.get("Config",{})
            # The public static preview has no C3 instance or model process.
            # Keep unknown no-instance containers fail-closed below.
            if config.get("Image")=="caddy:2" and config.get("Cmd")==["caddy","run","--config","/etc/caddy/Caddyfile","--adapter","caddyfile"]:
                continue
            args=" ".join(inspection.get("Args",[]))
            if any(x in args for x in ("/transport.py","/research.py","/market.py","worlds.cli")):
                continue
        try:
            state=json.loads(command(["docker","exec",name,"concorde3","call","/instance","state",'{"section":"config"}']))
            # state config is a wrapped record in C3's interface.
            cfg=state.get("config",state)
            cap=cfg["starts_per_hour"]
            total+=int(cap)
        except Exception as e:
            raise RuntimeError(f"cannot determine other instance ceiling: {name}") from e
    return total

def dispatched_cells(root):
    """Never re-run a behavioral outcome when resuming a setup-interrupted panel."""
    seen=set()
    for path in Path(root).glob("*/manifest.json"):
        m=read_json(path,root)
        observation=path.parent/"observation.json"
        statefile=path.parent/"subject/.concorde2/state.json"
        dispatched=False
        if observation.exists():dispatched=read_json(observation,root).get("telemetry",{}).get("dispatched",False)
        if statefile.exists():
            state=read_json(statefile,root)
            dispatched |= any(a.get("usage",{}).get("basis")!="constructed" and key not in m.get("inherited_activation_ids", []) for key,a in state.get("activations",{}).items())
        if dispatched:seen.add(cell_key(m))
    return seen

def cell_key(c):
    return tuple(c[k] for k in ("case","profile","variant","world_seed","model_profile","draw","arm"))

def telemetry(workspace, stopped, inherited=()):
    result={"container_stopped":stopped,"invocations":0,"tool_failures":[],"sessions":{},"exposure":{"served_context_ids":[],"tool_names":[]}}
    ids, names=set(),set()
    rt=workspace/".concorde2"
    try:
        result["model_preflight"] = read_json(rt/"lab-start.json",workspace)
        result["subscription_preflight"] = result["model_preflight"].get("auth_basis")=="subscription"
    except (OSError,ValueError):result["subscription_preflight"]=False
    for path in (rt/"contexts").glob("*.json"):
        if any(path.name.startswith(key + ".") for key in inherited): continue
        try:
            packet=read_json(path,workspace)
            ids.update(x["item"]["id"] for x in packet.get("items",[]))
        except (OSError,ValueError,KeyError): pass
    for path in (rt/"harness-logs").glob("*.jsonl"):
        if any(path.name.startswith(key + ".") for key in inherited): continue
        result["invocations"]+=1
        if not path.resolve().is_relative_to(workspace.resolve()): continue
        with path.open() as f:
            for line in f:
                try: ev=json.loads(line)
                except ValueError: continue
                item=ev.get("item",{})
                if item.get("type")=="mcp_tool_call": names.add(item.get("tool","unknown"))
                if item.get("type")=="mcp_tool_call" and (item.get("error") or item.get("status")=="failed"):
                    failure=item.get("error") or json.dumps(item.get("result",{}),ensure_ascii=False)[:2000]
                    result["tool_failures"].append({"log":path.name,"tool":item.get("tool"),"error":failure})
                if ev.get("type")=="thread.started":result["sessions"][path.name]=ev.get("thread_id")
                if ev.get("type")=="error": result.setdefault("harness_errors",[]).append(ev.get("message",""))
    result["exposure"]={"served_context_ids":sorted(ids),"tool_names":sorted(names),"interpretation":"served/retrieved traces only, not proof of comprehension"}
    return result

def stop_container(name):
    subprocess.run(["docker","stop","-t","5",name],capture_output=True,timeout=25)

def collect_sessions(name, base):
    # docker cp cannot reliably address this tmpfs. Read only transcript files
    # through the running container; never copy auth/session database material.
    script="""import pathlib,json,hashlib,os,stat
for p in pathlib.Path('/home/node/.codex/sessions').rglob('*.jsonl'):
 if p.is_symlink(): continue
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  st=os.fstat(f.fileno())
  if not stat.S_ISREG(st.st_mode): continue
  raw=f.read(16777216)
 print(json.dumps({'path':str(p),'source_bytes':st.st_size,'sha256':hashlib.sha256(raw).hexdigest(),'text':raw.decode(),'truncated':st.st_size>len(raw)}))
"""
    rows=[json.loads(x) for x in command(["docker","exec",name,"python3","-c",script]).splitlines()]
    target=Path(base)/("session-evidence-"+str(time.time_ns())+".json")
    save(target,rows)
    return {"path":str(target),"files":len(rows),"truncated":sum(r["truncated"] for r in rows)}

def collect_consumer_samples(workspace, samples, stopping, facts=None):
    while not stopping.is_set():
        try:
            report=read_json(workspace/"artifacts"/(facts["artifact"] if facts else "export.json"),workspace,max_bytes=1_000_000)
            correct=product_correct(report,facts["expected_product"]) if facts else export_correct(report)
        except (OSError,ValueError):correct=False
        samples.append({"at":stamp(),"correct":correct})
        stopping.wait(.5)

def collect_trajectory_samples(workspace, stats, stopping, facts):
    """Read-only independent receiving replay and changed-output history."""
    last = object()
    while not stopping.is_set():
        try: value=read_json(workspace/"artifacts"/facts["artifact"],workspace,max_bytes=1_000_000)
        except (OSError,ValueError):value=None
        at=stamp()
        if value!=last:
            stats.setdefault("decision_samples",[]).append({"at":at,"value":value})
            last=value
        if "expected_product" in facts:
            stats.setdefault("consumer_samples",[]).append({"at":at,"correct":product_correct(value,facts["expected_product"]),
                "initial_correct":product_correct(value,facts.get("initial_expected",facts["expected_product"]))})
        stopping.wait(.5)

def episode(args, cell, ledger):
    queued=time.monotonic()
    with ledger.slot() as lease:
        return execute_episode(args,cell,ledger,time.monotonic()-queued,lease)

def execute_episode(args, cell, ledger, queue_seconds, lease):
    run_id=uuid.uuid4().hex[:12]
    name="c3-lab-"+run_id
    base=Path(args.output)/run_id
    snapshot = getattr(args, "snapshot", None)
    if snapshot:
        from .trial_replay import restore
        restore(snapshot, base)
    else:
        base.mkdir(parents=True)
    ws,ex=base/"subject",base/"exchange"
    if snapshot:
        from .operation_continuation import materialize_operation
        from .memory_consequence import materialize_memory
        materializer = materialize_memory if cell["case"] == "MR01" else materialize_operation
        spec,files,exchange,facts=materializer(snapshot,cell["variant"],cell["profile"],cell["world_seed"])
    else:
        ws.mkdir(); ex.mkdir(); (ws/"artifacts").mkdir()
        spec,files,exchange,facts=materialize(cell["case"],cell["variant"],cell["profile"],cell["world_seed"])
    facts["entry"]=args.entry
    for item in ([] if snapshot else cell.get("image_practices",[])):
        if not any(c["item"]["id"]==item["id"] for c in spec["changes"]["items"]):
            spec["changes"]["items"].append({"expected_revision":1,"item":item})
    # Only the selected implementation/seed changes across matched arms.
    if cell["kit"]:
        spec["changes"]["items"]=[c for c in spec["changes"]["items"] if c["item"]["id"]!="rectification-practice"]
        spec["changes"]["items"].append({"expected_revision":1,"item":{"id":"rectification-practice","node":"rectification-kit","kind":"norm","status":"active","text":Path(cell["kit"]).read_text()}})
    model,effort=PROFILES[cell["model_profile"]]
    api = cell["model_profile"] == MUSE_PROFILE
    starts=1 if args.entry=="phase_probe" else args.starts
    wall=args.deadline+35 if args.entry=="phase_probe" else args.wall
    # A phase probe stops at the phase boundary, NOT by exhausting the self's
    # available work budget. Otherwise a sensible deferral is a false failure.
    inherited,start_ceiling,live_ceiling=execution_limits(spec,args.entry,starts,cell["case"])
    spec["config"].update({"workspace":True,"external_sandbox":True,"model":model,"effort":effort,"deadline_seconds":args.deadline,"starts_per_hour":start_ceiling,"concurrency":1,"freeze_at":stamp(wall+30)})
    if snapshot:
        spec["config"]["freeze_at"] = dt.datetime.fromtimestamp(int(time.time()+wall),dt.timezone.utc).isoformat().replace("+00:00","Z")
    if cell["arm"]=="candidate":spec["config"].update(json.loads(args.candidate_config))
    fixture=cell["fixture"]
    if not snapshot:
        for key,value in files.items(): save(ws/"artifacts"/key,value)
    for key,value in exchange.items(): save(ex/key,value)
    save(base/"seed.json",spec); save(base/"execution.json",{"entry":args.entry,"starts":starts,
        "effective_live_starts":live_ceiling,"starts_per_hour":start_ceiling,"wall_seconds":wall,
        "freeze_at":spec["config"]["freeze_at"],"maintain_until_deadline":cell["case"] in MAINTAINED,
        "allow_runtime_interruption":cell["case"]=="RC01"})
    if api:
        execution = read_json(base/"execution.json")
        execution.update(model_transport="explicit_api", model=model)
        save(base/"execution.json", execution)
    if snapshot:
        execution=read_json(base/"execution.json")
        execution.update(initialization="frozen_copy", maintain_until_deadline=True,
                         source_snapshot_sha256=facts["snapshot_sha256"],
                         source_state_sha256=hashlib.sha256((ws/".concorde2/state.json").read_bytes()).hexdigest())
        save(base/"execution.json",execution)
        save(base/"fork.json",{"snapshot_sha256":facts["snapshot_sha256"],"status":"preparing"})
        save(base/"fork-spec.json",{"cutoff":dt.datetime.fromisoformat(execution["freeze_at"].replace("Z","+00:00")).timestamp(),
             "model":model,"effort":effort,"starts":starts,"source_manifest":facts["snapshot_sha256"],
             "manifest_path":"/run/fork.json","resume_programs":args.resume_programs.split(",")})
    save(base/"facts.json",facts)
    image=command(["docker","image","inspect",cell["image"],"--format","{{.Id}}"])
    manifest={**cell,"id":run_id,"model":model,"effort":effort,"image_id":image,"at":stamp(),"seed_sha256":digest(spec),"world_sha256":digest(facts["semantic_signature"]),"runner_sha256":digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((Path(args.output)/"assets").glob("*.py"))}),"resolved_model_revision":"unknown","entry":args.entry,"max_live_starts":starts,"reserved_invocations":(start_ceiling-1)*2,"deadline_seconds":args.deadline,"wall_budget_seconds":wall,"assistance":"constructed settled snapshot" if spec.get("settled_checkpoint") else "constructed recovery checkpoint, no prior conversation", "fixture_sha256":hashlib.sha256(Path(fixture).read_bytes()).hexdigest(),"config_override":json.loads(args.candidate_config) if cell["arm"]=="candidate" else {}}
    manifest.update(max_live_starts=live_ceiling,reserved_invocations=invocation_reservation(spec['config'],live_ceiling),
                    kit_sha256=hashlib.sha256(Path(cell["kit"]).read_bytes()).hexdigest() if cell["kit"] else None,
                    assistance=manifest["assistance"] if inherited else "constructed starting world; no inherited activation")
    if snapshot:
        manifest.update(snapshot_sha256=facts["snapshot_sha256"], inherited_activation_ids=facts["inherited_activation_ids"],
                        assistance=facts["fixture_origin"], resume_programs=args.resume_programs.split(","))
        manifest["runtime_qualification"] = getattr(args, "snapshot_runtimes", {}).get(image)
    save(base/"manifest.json",manifest)
    other=other_capacity()
    ledger.reserve(run_id,manifest["reserved_invocations"],other)
    lease.seek(0);lease.truncate()
    lease.write(json.dumps({"container":name}));lease.flush();os.fsync(lease.fileno())
    began=time.monotonic()
    net,proxy=name+"-net",name+"-transport"
    timer=name+"-stop"
    state,arts,stats={}, {}, {}
    errors=[]
    created=False
    dispatched=False
    observing=threading.Event()
    consumer=None
    try:
        create_network(net)
        from evals.transport import preview_mount_args
        proxy_command=["docker","run","-d","--name",proxy,"--label","concorde.lab=true","--network","bridge","--memory","128m","--cpus",".25","--pids-limit","64","--cap-drop","ALL","--security-opt","no-new-privileges","--mount",f"type=bind,source={Path(args.output)/'assets/transport.py'},target=/transport.py,readonly",*preview_mount_args()]
        if api:
            proxy_command += ["--mount", f"type=bind,source={Path(args.api_broker_token).resolve()},target=/run/api-broker-token,readonly"]
        proxy_command += ["--entrypoint","python3",image,"/transport.py"]
        if api: proxy_command += ["--api-upstream",args.api_broker,"--api-token-file","/run/api-broker-token"]
        command(proxy_command)
        command(["docker","network","connect",net,proxy])
        proxy_ip=command(["docker","inspect",proxy,"--format",'{{(index .NetworkSettings.Networks "'+net+'").IPAddress}}'])
        cmd=["docker","create","--name",name,"--label","concorde.lab=true","--network",net,"--dns","127.0.0.1","--memory","2g","--cpus","1","--pids-limit","256","--cap-drop","ALL","--security-opt","no-new-privileges","--read-only","--tmpfs","/tmp:rw,size=256m,mode=1777","--tmpfs","/home/node/.codex:rw,size=512m,uid=1000,gid=1000,mode=700","--env","CODEX_HOME=/home/node/.codex","--env",f"HTTPS_PROXY=http://{proxy_ip}:8080","--env",f"HTTP_PROXY=http://{proxy_ip}:8080","--env",f"NO_PROXY=localhost,127.0.0.1,{proxy_ip}"]
        mounts=[(ws,"/instance",False),(ex,"/exchange",True),(Path(fixture),"/run/fixture",True),(base/"seed.json","/run/seed.json",True),(base/"execution.json","/run/execution.json",True),(Path(args.output)/"assets/driver.py","/run/driver.py",True),session_mount(ws)]
        if api:
            mounts.append((Path(args.output)/"assets/muse.py", "/run/muse.py", True))
            spec["config"].update(harness="command", command=["python3", "/run/muse.py", "--endpoint", f"http://{proxy_ip}:8080/v1/chat/completions", "--model", model, "--binary", "/usr/local/bin/concorde3", "--workspace"])
            save(base/"seed.json", spec)
            manifest.update(seed_sha256=digest(spec), model_transport="explicit_api", adapter_sha256=hashlib.sha256((Path(args.output)/"assets/muse.py").read_bytes()).hexdigest())
        else:
            mounts.append((Path(args.auth),"/run/subscription-auth.json",True))
        if snapshot:
            mounts.extend((base/name,"/run/"+name,True) for name in ("fork.json","fork-spec.json"))
        for src,dst,ro in mounts: cmd += ["--mount",f"type=bind,source={src.resolve()},target={dst}"+(",readonly" if ro else "")]
        cmd += ["--entrypoint","sleep",image,str(wall+90)]
        command(cmd); created=True
        # An operator-owned system timer can stop escaped workloads even if this
        # runner crashes. Only this exact, newly created container is in scope.
        command(["sudo","-n","systemd-run","--quiet","--unit",timer,"--on-active",f"{wall+75}s","--timer-property=AccuracySec=1s","docker","stop","-t","5",name,proxy])
        command(["docker","start",name])
        # Prove lack of general direct/proxied egress and evaluator storage.
        probe="import socket,urllib.request,pathlib;\nassert not pathlib.Path('/run/facts.json').exists();\ntry:\n socket.create_connection(('1.1.1.1',443),2); raise AssertionError('direct egress available')\nexcept OSError: pass\ntry:\n urllib.request.urlopen('https://example.com',timeout=4); raise AssertionError('proxy allowed general egress')\nexcept urllib.error.URLError: pass\nprint('isolation-qualified')"
        command(["docker","exec",name,"python3","-c",probe],timeout=15)
        manifest["isolation"]="separate internal network; no general direct/proxied egress; " + ("fixed API broker POST route; no credential in subject" if api else "fixed subscription CONNECT allowlist") + "; no evaluator mount"
        save(base/"manifest.json",manifest)
        dispatched=True # Conservative even if Popen/transport fails after this boundary.
        proc=subprocess.Popen(["docker","exec",name,"python3","/run/driver.py"],stdout=(base/"driver.stdout").open("w"),stderr=(base/"driver.stderr").open("w"))
        intervention=None
        if snapshot:
            from .systemization_cases import collect_systemization
            from .memory_consequence import collect_memory
            collector = collect_memory if cell["case"] == "MR01" else collect_systemization
            consumer=threading.Thread(target=collector,args=(ws,ex,stats,observing,facts))
            consumer.start()
        if args.entry=="episode" and cell["case"] in ("SY02","SY03","SY04","SY05","SY06","AC01","GC01"):
            from .lifelike_cases import collect_lifelike
            consumer=threading.Thread(target=collect_lifelike,args=(ws,ex,stats,observing,facts))
            consumer.start()
        if args.entry=="episode" and cell["case"] in ("SY01","RC01","BP02","BP03"):
            if cell["case"] == "SY01":
                from .systemization_cases import collect_systemization
                consumer=threading.Thread(target=collect_systemization,args=(ws,ex,stats,observing,facts))
            elif cell["case"] == "RC01":
                from .recovery_cases import collect_recovery
                consumer=threading.Thread(target=collect_recovery,args=(ws,base,stats,observing,facts))
            else:
                consumer=threading.Thread(target=collect_trajectory_samples,args=(ws,stats,observing,facts))
            consumer.start()
        if args.entry=="episode" and cell["case"] in ("FC01","HD01","AU02","BP01","PC01","UX01","UX02","AR01","AR02","AR03","AR04","AR05","AR06","AR07","AR08","AR09"):
            stats["consumer_samples"]=[]
            if cell["case"] == "UX02":
                from .receiving_feedback import collect_receiving_feedback
                consumer=threading.Thread(target=collect_receiving_feedback,args=(ws,ex,stats,observing,facts))
            elif cell["case"] in ("AR01","AR02","AR03","AR04","AR05","AR06","AR07","AR08","AR09"):
                from .adaptation_observe import collect_adaptation
                consumer=threading.Thread(target=collect_adaptation,args=(ws,base,stats,observing,facts))
            else:
                consumer=threading.Thread(target=collect_consumer_samples,args=(ws,stats["consumer_samples"],observing,facts)) if cell["case"]=="FC01" else threading.Thread(target=collect_trajectory_samples,args=(ws,stats,observing,facts))
            consumer.start()
            if facts.get("intervention"):
                def deliver_after_completion():
                    while not observing.wait(.25):
                        try:
                            current=read_json(ws/".concorde2/state.json",ws)
                            completed=any(a["status"]=="completed" and a.get("usage",{}).get("basis")!="constructed" for a in current["activations"].values())
                        except (OSError,ValueError):continue
                        if completed:
                            if observing.wait(facts["intervention"]["delay_seconds"]):return
                            save(ex/facts["intervention"].get("file","inbox.json"),facts["intervention"]["payload"])
                            stats.setdefault("interventions",[]).append({"at":stamp(),"interface":"/exchange/inbox.json","trigger":"after first completion; ordinary mail, no scheduler wake"})
                            save(base/"interventions.json",stats["interventions"])
                            if facts.get("followup"):
                                if observing.wait(facts["followup"]["delay_after_first_delivery_seconds"]):return
                                save(ex/facts["followup"].get("file","inbox.json"),facts["followup"]["payload"])
                                stats["interventions"].append({"at":stamp(),"interface":"/exchange/inbox.json","trigger":"fixed delay after first delivery; ordinary mail, no scheduler wake"})
                                save(base/"interventions.json",stats["interventions"])
                            return
                intervention=threading.Thread(target=deliver_after_completion)
                intervention.start()
        if args.entry=="episode" and cell["case"].startswith("AT"):
            stats["consumer_samples"]=[]
            consumer=threading.Thread(target=collect_consumer_samples,args=(ws,stats["consumer_samples"],observing,facts))
            consumer.start()
            if facts.get("intervention"):
                change=facts["intervention"]
                def deliver_attention():
                    save(ex/change["file"],change["payload"])
                    stats.setdefault("interventions",[]).append({"at":stamp(),"interface":"/exchange/"+change["file"],"event":"authorized customer request","trigger":"ordinary inbox, no private wake"})
                intervention=threading.Timer(change["after_seconds"],deliver_attention)
                intervention.start()
        if args.entry=="episode" and cell["case"]=="BD01" and cell["variant"]=="challenge":
            stats["consumer_samples"]=[]
            consumer=threading.Thread(target=collect_consumer_samples,args=(ws,stats["consumer_samples"],observing))
            consumer.start()
            def deliver():
                save(ex/"inbox.json",[{"from":"authorized customer","id":"request-currency","request":"Please preserve the accepted names and amounts and add currency as the third export column, with USD for both rows, in artifacts/export.json. This is covered by our ongoing agreement."}])
                stats.setdefault("interventions",[]).append({"at":stamp(),"interface":"/exchange/inbox.json","event":"authorized currency-column request","trigger":"60 seconds after episode dispatch; no private wake"})
            intervention=threading.Timer(60,deliver)
            intervention.start()
        try: proc.wait(timeout=wall+45)
        except subprocess.TimeoutExpired:
            stats["timed_out"]=True
            stop_container(name); proc.wait(timeout=15)
        finally:
            if intervention:
                if isinstance(intervention,threading.Timer):intervention.cancel()
                else:observing.set()
                intervention.join()
        if proc.returncode: errors.append(f"driver exit {proc.returncode}")
        command(["docker","exec",name,"/run/fixture","validate","/instance"])
        try:stats["session_capture"]=collect_sessions(name,base)
        except Exception as error:stats["session_capture_error"]=str(error)
    except Exception as exc:
        errors.append(str(exc)+(": "+exc.stderr[-2000:] if isinstance(exc,subprocess.CalledProcessError) and exc.stderr else ""))
    finally:
        if created: stop_container(name)
        observing.set()
        if consumer:consumer.join(timeout=10)
        stopped=True if not created else command(["docker","inspect",name,"--format","{{.State.Running}}"])=="false"
        stop_container(proxy)
        # Remove only our stopped transport; keep subject/evidence for diagnosis.
        subprocess.run(["docker","rm",proxy],capture_output=True,timeout=20)
        if created: subprocess.run(["docker","network","disconnect",net,name],capture_output=True,timeout=20)
        subprocess.run(["docker","network","rm",net],capture_output=True,timeout=20)
        subprocess.run(["sudo","-n","systemctl","stop",timer+".timer"],capture_output=True,timeout=15)
        try:
            state=read_json(ws/".concorde2/state.json",ws)
            for key in set(files)|({facts["artifact"]} if facts.get("artifact") else set()):
                try: arts[key]=read_json(ws/"artifacts"/key,ws)
                except (OSError,ValueError): arts[key]=None
        except (OSError,ValueError) as exc: errors.append(str(exc))
        stats.update(telemetry(ws,stopped,manifest.get("inherited_activation_ids",[])))
        stats["errors"]=errors
        stats["dispatched"]=dispatched
        if not dispatched: ledger.void_undispatched(run_id,"runner never reached driver dispatch boundary; retained errors in observation.json")
        stats["timed_out"]=stats.get("timed_out",False) or any("deadline" in a.get("summary","").lower() for key,a in state.get("activations",{}).items() if key!="act.checkpoint" and key not in manifest.get("inherited_activation_ids",[]))
        result=outcome(facts,state,arts,stats)
        if errors and result["label"] not in ("invalid_fixture","deadline_censored"): result["label"]="runtime_failure"
        record={**manifest,"queue_seconds":queue_seconds,"wall_seconds":time.monotonic()-began,"telemetry":stats,"result":result}
        # Freeze independent host observations. Regraders write a new file/version.
        save(base/"observation.json",{"state":state,"artifacts":arts,"telemetry":stats})
        save(base/"result.json",record)
        print(json.dumps({"id":run_id,"case":cell["case"],"arm":cell["arm"],"model":model,"label":result["label"],"seconds":round(record["wall_seconds"],1)}),flush=True)
    return record

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    run=sub.add_parser("run")
    run.add_argument("--output",required=True,type=lambda s:str(Path(s).resolve()))
    run.add_argument("--auth",type=lambda s:str(Path(s).resolve()),help="Subscription auth; required only for subscription profiles")
    run.add_argument("--api-broker",help="Explicit host broker HTTP endpoint; Muse experiment only, never a fallback")
    run.add_argument("--api-broker-token",type=lambda s:str(Path(s).resolve()),help="Scoped broker token mounted only in transport sidecar")
    run.add_argument("--api-adapter",type=lambda s:str(Path(s).resolve()),help="Explicit Muse command adapter source; copied and hashed")
    run.add_argument("--fixture",default=str(ROOT/"bin/lab-fixture"))
    run.add_argument("--image",default="concorde3:lab-current")
    run.add_argument("--candidate-image")
    run.add_argument("--candidate-fixture",help="Fixture linked against candidate core; avoids contaminating baseline state/schema")
    run.add_argument("--candidate-config",default="{}",help="Declared candidate-only JSON config, e.g. accelerated deferral clocks")
    run.add_argument("--candidate-kit")
    run.add_argument("--snapshot", type=lambda s:str(Path(s).resolve()), help="Qualified frozen file-interface snapshot; SY07/MR01 only, never reseeded")
    run.add_argument("--snapshot-runtime-witness", action="append", default=[], help="Explicit no-model qualification receipt for each changed snapshot runtime; never reseeds the source")
    run.add_argument("--resume-programs", help="Explicit comma-separated stopped registrations to resume in an isolated snapshot copy")
    run.add_argument("--aa",action="store_true",help="Randomized no-change A/A")
    run.add_argument("--single-arm",choices=("baseline","candidate"),default="baseline",help="Label an explicitly separate single-arm panel")
    run.add_argument("--models",default="luna6-max",help="Comma-separated exact profiles; new selves and sim tests default to Luna 6 max. Legacy profiles remain pinned for reproduction.")
    run.add_argument("--cases",required=True,help="Explicit case selection; use evals.readiness plan for the economical qualification screen")
    run.add_argument("--profiles",default="situated")
    run.add_argument("--variants",default="challenge")
    run.add_argument("--worlds",default="1")
    run.add_argument("--draws",type=int,default=1,help="One or two independent shots; retain every result, no retry-until-green")
    run.add_argument("--draw-offset",type=int,default=0,help="Use 1 for a separately dispatched second shot; offset+draws cannot exceed 2")
    run.add_argument("--workers",type=int,default=4)
    run.add_argument("--deadline",type=int,default=180)
    run.add_argument("--entry",choices=["phase_probe","episode"],default="phase_probe")
    run.add_argument("--starts",type=int,default=2,help="Requested starts; maintained episodes retain a three-start floor and report effective_live_starts")
    run.add_argument("--wall",type=int,default=420)
    run.add_argument("--ledger",default=os.environ.get('CONCORDE_LAB_LEDGER',str(Path.home()/'.local/share/concorde/behavioral-lab/admissions.jsonl')))
    run.add_argument("--pool-cap",type=int,default=200,help="Aggregate reserved model invocations/hour; per-instance caps unchanged; owner ceiling 5000/h remains a ceiling, not a target")
    run.add_argument("--exclude-dispatched-from",action="append",type=lambda s:str(Path(s).resolve()),help="Resume setup-interrupted panel in a new directory; repeat for multiple prior directories; never replay dispatched cells")
    args=p.parse_args()
    if args.cases == 'BP03' and args.entry != 'episode':p.error('BP03 requires a complete episode')
    api_requested = MUSE_PROFILE in args.models.split(",")
    if api_requested:
        if args.models != MUSE_PROFILE or not all((args.api_broker, args.api_broker_token, args.api_adapter)) or args.auth:
            p.error("Muse requires an API-only panel, explicit broker/token/adapter, and no subscription credential")
        from urllib.parse import urlsplit
        endpoint = urlsplit(args.api_broker)
        if endpoint.scheme != "http" or not endpoint.hostname or endpoint.path != "/v1/chat/completions" or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            p.error("broker must be an explicit credential-free HTTP chat-completions endpoint")
        if not all(Path(v).is_file() for v in (args.api_broker_token, args.api_adapter)):
            p.error("API broker token and adapter must exist")
    elif not args.auth or any((args.api_broker, args.api_broker_token, args.api_adapter)):
        p.error("subscription profiles require auth and cannot use API settings")
    if args.snapshot or set(args.cases.split(",")) & {"SY07", "MR01"}:
        memory = args.cases == "MR01"
        if (not args.snapshot or args.cases not in ("SY07", "MR01") or args.entry != "episode" or args.starts != (4 if memory else 6)
            or args.wall != (1200 if memory else 2700) or args.deadline != 300 or args.profiles != "situated"
            or not set(args.models.split(",")).issubset({"luna-xhigh", "terra-medium"} if memory else {"luna-xhigh"})
            or not args.resume_programs or args.aa or args.candidate_fixture
            or args.candidate_kit or json.loads(args.candidate_config)):
            p.error("Snapshot cases require qualified source/programs, episode, deadline 300, situated, no kit/config/fixture changes; SY07 starts 6/wall 2700/Luna xhigh; MR01 starts 4/wall 1200/Luna xhigh or Terra medium")
        from .trial_replay import load, validate
        captured=load(args.snapshot)
        data=validate(captured,Path(args.snapshot).parent.parent.parent)
        source=json.loads(data["subject/.concorde2/state.json"])
        selected=args.resume_programs.split(",")
        if len(set(selected)) != len(selected) or set(selected)-set(source["programs"]): p.error("unknown or duplicate resume program")
        if memory:
            from .memory_consequence import PROGRAMS
            if set(selected) != set(PROGRAMS): p.error("MR01 resumes all four inherited service/observation registrations")
        if any(dt.datetime.fromisoformat(a["started"].replace("Z","+00:00")).timestamp()>time.time()-3600 for a in source["activations"].values()):
            p.error("Snapshot cases require inherited starts to age out; do not reset historical accounting")
        from .operation_continuation import runtime_witnesses, require_runtime
        try:
            witnesses=runtime_witnesses(args.snapshot_runtime_witness,
                hashlib.sha256(Path(args.snapshot).read_bytes()).hexdigest(), captured["container"]["image"],
                hashlib.sha256(Path(args.fixture).read_bytes()).hexdigest(), family=args.cases)
            args.snapshot_runtimes={}
            resolved={}
            for requested in {args.image, args.candidate_image}-{None}:
                image=command(["docker","image","inspect",requested,"--format","{{.Id}}"])
                args.snapshot_runtimes[image]=require_runtime(image,captured["container"]["image"],witnesses)
                resolved[requested]=image
            # Bind the exact inspected images now, not mutable tags resolved later.
            args.image=resolved[args.image]
            if args.candidate_image:
                args.candidate_image=resolved[args.candidate_image]
        except (ValueError, OSError) as error:
            p.error(str(error))
    elif args.resume_programs:
        p.error("resume-programs requires a qualified snapshot")
    elif args.snapshot_runtime_witness:
        p.error("snapshot-runtime-witness requires a qualified snapshot")
    if not 1 <= args.pool_cap <= HOST_CALL_CEILING:p.error("pool-cap must be between 1 and the authorized 5000/h ceiling")
    candidate_config=json.loads(args.candidate_config)
    if not isinstance(candidate_config,dict) or set(candidate_config)-{"deferral_seconds","reconsider_seconds","work_reentry"}:p.error("only declared attention clocks and optional work_reentry may differ in candidate config")
    if "work_reentry" in candidate_config and type(candidate_config['work_reentry']) is not bool:p.error("work_reentry must be boolean")
    if args.entry=="phase_probe" and any(c.startswith("AT") for c in args.cases.split(",")):p.error("AT cases require --entry episode; they measure actual outcomes and quiet intervals")
    if "AR09" in args.cases.split(",") and (args.entry != "episode" or args.wall < 900 or args.starts < 4):
        p.error("AR09 requires an uncensored episode: wall >=900, starts >=4")
    if args.entry=="phase_probe" and any(c in ("SY02","SY03","SY04","SY05","SY06","AC01","GC01") for c in args.cases.split(",")):p.error("Lifelike cases require --entry episode")
    if args.entry=="phase_probe" and any(c in ("FC01","CF01","LR01","RG01","RG02","AU01","HD01","AU02","BP01","PC01","UX01","UX02","AR01","AR02","AR03","AR04","SY01","RC01","BP02") for c in args.cases.split(",")):p.error("Repair/trajectory cases require --entry episode")
    if not 1<=args.workers<=EPISODE_SLOTS or args.deadline<30 or args.wall>=3600 or args.starts<1 or not 1<=args.draws<=2 or args.draw_offset<0 or args.draw_offset+args.draws>2: p.error("invalid resource limits; maximum two shots")
    if "SY06" in args.cases.split(",") and (args.starts != 8 or args.wall != 3300):
        p.error("SY06's declared arrangement requires --starts 8 --wall 3300; qualify a separately versioned variation for other limits")
    if Path(args.output).exists() and any(Path(args.output).iterdir()): p.error("output must be new/empty; never overwrite a run")
    Path(args.output).mkdir(parents=True,exist_ok=True)
    (Path(args.output)/"assets").mkdir()
    for path in HERE.glob("*.py"): shutil.copyfile(path,Path(args.output)/"assets"/path.name)
    if api_requested: shutil.copyfile(args.api_adapter,Path(args.output)/"assets/muse.py")
    fixture_copy=Path(args.output)/"assets/lab-fixture"
    shutil.copy2(args.fixture,fixture_copy)
    args.fixture=str(fixture_copy)
    if args.candidate_fixture:
        candidate_copy=Path(args.output)/"assets/candidate-fixture"
        shutil.copy2(args.candidate_fixture,candidate_copy)
        args.candidate_fixture=str(candidate_copy)
    if args.candidate_kit:
        kit_copy=Path(args.output)/"assets/candidate-kit.md"
        shutil.copyfile(args.candidate_kit,kit_copy)
        args.candidate_kit=str(kit_copy)
    arms=comparison_arms(args)
    cells=[]
    for case in args.cases.split(","):
      for profile in args.profiles.split(","):
       for variant in args.variants.split(","):
        for world in map(int,args.worlds.split(",")):
         for model in args.models.split(","):
          if model not in PROFILES: p.error("unknown exact model profile")
          for draw in range(args.draw_offset,args.draw_offset+args.draws):
           for arm,image,kit in arms:
            cells.append(dict(case=case,profile=profile,variant=variant,world_seed=world,model_profile=model,draw=draw,arm=arm,image=image,kit=kit,fixture=(args.candidate_fixture or args.fixture) if arm=="candidate" else args.fixture))
    random.Random(8147).shuffle(cells)
    if args.exclude_dispatched_from:
        excluded=set().union(*(dispatched_cells(path) for path in args.exclude_dispatched_from))
        cells=[c for c in cells if cell_key(c) not in excluded]
        save(Path(args.output)/"resume.json",{"source":args.exclude_dispatched_from,"excluded_dispatched":sorted(excluded),"reason":"Only undispatched cells may resume; all prior behavior retained"})
    for image in {c["image"] for c in cells}:
        resolved=command(["docker","image","inspect",image,"--format","{{.Id}}"])
        practices=image_practices(resolved)
        for cell in cells:
            if cell["image"]==image:cell["requested_image"]=image;cell["image"]=resolved;cell["image_practices"]=practices
    save(Path(args.output)/"plan.json",{"cells":cells,"limits":{"workers":args.workers,"deadline":args.deadline,"entry":args.entry,"starts":args.starts,"wall":args.wall,"pool_cap":args.pool_cap},"planned_at":stamp(),"rules":"All draws retained. No retries-until-green. No automatic LLM judge."})
    ledger=Ledger(args.ledger,cap=args.pool_cap)
    # Preflight aggregate capacity before spending any live invocations.
    other_capacity()
    results=[]
    setup_failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(episode,args,cell,ledger) for cell in cells]
        future_cells=dict(zip(futures,cells))
        for future in concurrent.futures.as_completed(futures):
            try:result=future.result()
            except Exception as error:
                setup_failures.append({"cell":future_cells[future],"error":str(error),"classification":"runner failure; inspect dispatch evidence before any resume"})
                save(Path(args.output)/"runner-failures.json",setup_failures)
                continue
            results.append(result)
            save(Path(args.output)/"results.json",results)
            (Path(args.output)/"report.md").write_text(markdown(results))
    if setup_failures:raise RuntimeError(f"{len(setup_failures)} runner failures retained; panel incomplete")

if __name__=="__main__": main()

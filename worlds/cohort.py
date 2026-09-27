"""Finite parallel calibration, descriptive monitoring, no automatic promotion."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import secrets
import subprocess
import sys
import time

from host_runtime import command, save
from .engine import World
from .observe import report
from .shutdown import freeze
from .store import encoded
from . import scenarios

REPO = Path(__file__).resolve().parents[1]
ARMS = (("market-11", "market", 11, 60, 3), ("market-12", "market", 12, 60, 3),
        ("research-11", "research", 11, 20, 2), ("coordination-12", "coordination", 12, 20, 2),
        ("consumer-11", "consumer", 11, 40, 2))
DEPTH_ARMS = (("market-21", "market", 21, 110, 4),
              ("research-21", "research", 21, 30, 2),
              ("consumer-21", "consumer", 21, 60, 2))


def reference_controls(world):
    """Separate noncognitive vendors; never credit a C3 for fixture delivery."""
    project = world.view("northstar", "project")["records"][0]
    controls = (("reference-plain", "Compact importer", scenarios.baseline(project["task"]), 2),
                ("reference-fluent", "Project accelerator", {"message": "All project work is perfectly completed!"}, 5),
                ("reference-premium", "Prepared export", scenarios.baseline(project["task"]), 8))
    for actor, title, content, price in controls:
        with world.s.transaction() as db:
            info = {"name": title, "role": "reference", "purpose": "A fixed experimental reference offering, not a live independent company"}
            db.execute("INSERT INTO actors VALUES (?,?,?)", (actor, secrets.token_urlsafe(32), encoded(info)))
            world.s.put(db, "profile", actor, {"name": title, "purpose": info["purpose"]}, ["*"], "profile:"+actor)
            world.s.put(db, "quota", actor, {"bytes": 100000}, record_id="quota:"+actor)
            world.s.event(db, "_operator", "reference_vendor", {"actor": actor, "basis": "disposable control, never C3 performance"})
        artifact = world.act(actor, "reference-output", {"op": "artifact", "title": title, "content": content, "audience": ["northstar"]})["result"]
        world.act(actor, "reference-offer", {"op": "offer", "title": title, "price": price, "buyer": "northstar", "delivery": artifact["id"],
            "terms": "Prepared output for the current import. Sample inspection and a trial are available before purchase. No future compatibility or support promise."})


def prepare(root, hours=2, image="concorde3:lab-current", period_seconds=3600, profile="breadth", baseline_image=None):
    if profile not in ("breadth", "feedback-depth", "continuity-comparison"):
        raise ValueError("unknown cohort profile")
    if profile == "continuity-comparison" and not baseline_image:
        raise ValueError("matched comparison requires an explicit baseline image")
    if type(period_seconds) is not int or period_seconds < 60:
        raise ValueError("scenario period must be an integer >= 60 seconds")
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("fresh cohort directory required")
    root.mkdir(parents=True, mode=0o700)
    started = time.time()
    manifest = {"started": started, "cutoff": started+hours*3600, "status": "prepared", "worlds": {},
                "image": command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]),
                "source_revision": command(["git", "rev-parse", "HEAD"], cwd=REPO),
                "shared_calls_per_hour": 200, "shared_concurrency": 8,
                "purpose": "Parallel world qualification and exploratory C3 baselines; no automated selection or live-money promotion"}
    manifest["profile"] = profile
    arms=DEPTH_ARMS if profile=="feedback-depth" else ARMS
    if profile=="continuity-comparison":
        arms=(("market-baseline-31","market",31,100,4),("market-candidate-31","market",31,100,4))
        baseline=command(["docker","image","inspect",baseline_image,"--format","{{.Id}}"])
        manifest["world_images"]={"market-baseline-31":baseline,"market-candidate-31":manifest["image"]}
        manifest["purpose"]="Matched-world exploratory comparison of continuation/trajectory context; customer choices remain stochastic, no automated promotion"
    for index, (name, pack, seed, calls, concurrency) in enumerate(arms):
        world = World(root/name)
        world.create(pack, seed, hours)
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg.update(started=started, cutoff=manifest["cutoff"], period_seconds=period_seconds, staff_per_period=24,
                       calls_per_hour=calls, concurrency=concurrency, shared_concurrency=8, counterpart_calls_per_hour=4, counterpart_response_reserve=2,
                       experiment="parallel-calibration-v2", baseline_starts=6, maximum_starts=12)
            if profile in ("feedback-depth", "continuity-comparison"):
                cfg.update(counterpart_calls_per_hour=8, counterpart_response_reserve=6, experiment="integrity-agency-depth-v1")
            if profile=="continuity-comparison":cfg["experiment"]="trajectory-continuity-v1"
            world.s.meta(db, "config", cfg)
            for n, p in enumerate(world.s.rows(db, "project")):
                world.s.revise(db, p, staff_remaining=24)
                c = world.s.get(db, "continuation:"+p["owner"])
                world.s.revise(db, c, next_at=started+45+(0 if profile=="continuity-comparison" else index*10)+n*20, reason="Staggered initial review; ordinary workload already operates")
            world.s.event(db, "_operator", "protocol", {"config": cfg, "reason": "Parallel breadth requested; declared scenario period with proportionate staff; preserve real deadlines and authority"})
        manifest["worlds"][name] = str(world.s.root)
        if pack == "market":
            reference_controls(world)
    save(root/"cohort.json", manifest)
    return manifest


def snapshot(root):
    root = Path(root)
    m = json.loads((root/"cohort.json").read_text())
    result = {"at": time.time(), "basis": "Automated descriptive health, not a competence verdict", "worlds": {}}
    for name, path in m["worlds"].items():
        try:
            w = World(Path(path))
            r = report(w)
            subjects = {}
            for statefile in (w.s.root/"subjects").glob("*/.concorde2/state.json"):
                state = json.loads(statefile.read_text())
                activations = list(state["activations"].values())
                subjects[statefile.parent.parent.name] = {"mode": state.get("mode"), "model": state["config"]["model"],
                    "completed": sum(a["status"] == "completed" for a in activations), "failed": sum(a["status"] == "failed" for a in activations),
                    "summaries": [{"at": a.get("finished"), "summary": a.get("summary", "")[:2000]} for a in activations[-3:]],
                    "usage": [a.get("usage") for a in activations]}
            r["subjects"] = subjects
            result["worlds"][name] = r
        except Exception as error:
            result["worlds"][name] = {"health_error": str(error)}
    directory = root/"checkpoints"
    save(directory/(str(int(result["at"]))+".json"), result)
    with (root/"running-log.jsonl").open("a") as f:
        f.write(json.dumps(result)+"\n"); f.flush(); os.fsync(f.fileno())
    stamp = dt.datetime.fromtimestamp(result["at"], dt.timezone.utc).isoformat()
    with (root/"running-log.md").open("a") as f:
        f.write("\n## "+stamp+" — automated health snapshot\n\n")
        for name, r in result["worlds"].items():
            if "health_error" in r:
                f.write("- "+name+": health read failed: "+r["health_error"]+"\n")
            else:
                f.write(f'- {name}: frozen={r["frozen"]}; calls={r["calls"]}; pending={len(r["unresolved_calls"])}; events={r["events"]}.\n')
        f.write("\nInterpretation: counts alone do not establish competence. Review delivered artifacts, actual use, failures, exposure and operator assistance before changing C3.\n")
        f.flush(); os.fsync(f.fileno())
    return result


def run(root, attach=False):
    root = Path(root).resolve()
    m = json.loads((root/"cohort.json").read_text())
    if m["status"] != ("running" if attach else "prepared"):
        raise ValueError("no implicit cohort restart")
    stop = False
    def stopping(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    children = {}
    streams = []
    m["status"] = "running"
    save(root/"cohort.json", m)
    try:
        for name, path in m["worlds"].items():
            log = (root/(name+".controller.log")).open("a")
            streams.append(log)
            children[name] = subprocess.Popen([sys.executable, "-m", "worlds.cli", "attach" if attach else "run", path, "--image", m.get("world_images",{}).get(name,m["image"])], cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        last = time.monotonic()
        snapshot(root)
        failed = set()
        while not stop and time.time()<m["cutoff"]:
            for name, child in children.items():
                if child.poll() is not None and name not in failed:
                    failed.add(name)
                    freeze(World(Path(m["worlds"][name])))
                    with (root/"running-log.md").open("a") as f:
                        f.write(f'\nController {name} exited {child.returncode}; scoped freeze applied. Review infrastructure before behavioral attribution.\n')
                    snapshot(root)
            if time.monotonic()-last>=1800:
                snapshot(root)
                last = time.monotonic()
            if len(failed)==len(children):
                break
            time.sleep(5)
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        errors = {}
        for name, path in m["worlds"].items():
            try: freeze(World(Path(path)))
            except Exception as error: errors[name] = str(error)
        for child in children.values():
            try: child.wait(timeout=45)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        # Close the creation/shutdown race after all dispatchers have exited.
        for name, path in m["worlds"].items():
            try: freeze(World(Path(path))); errors.pop(name, None)
            except Exception as error: errors[name] = str(error)
        m.update(status="freeze_error" if errors else "frozen", errors=errors, finished=time.time())
        save(root/"cohort.json", m)
        snapshot(root)
        for stream in streams: stream.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare", "run", "attach", "snapshot"))
    p.add_argument("root", type=Path)
    p.add_argument("--hours", type=float, default=2)
    p.add_argument("--image", default="concorde3:lab-current")
    p.add_argument("--profile", choices=("breadth", "feedback-depth", "continuity-comparison"), default="breadth")
    p.add_argument("--baseline-image")
    p.add_argument("--period-seconds", type=int, default=3600)
    args = p.parse_args()
    result = {"prepare": lambda: prepare(args.root, args.hours, args.image, args.period_seconds, args.profile,args.baseline_image), "run": lambda: run(args.root), "attach": lambda: run(args.root, attach=True), "snapshot": lambda: snapshot(args.root)}[args.action]()
    if result is not None: print(json.dumps(result, indent=2))

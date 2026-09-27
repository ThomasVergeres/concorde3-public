"""Matched, finite Terra lives; image-only organizational-practice comparison.

No automatic competence verdict, API fallback, relaunch, or source updates.
Prepare/run must execute from a pinned controller checkout. Evidence stays private.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from evals.lab import command, save
from worlds import campaign, forensics, scenarios, organizational_exposure
from worlds.engine import World
from worlds.store import digest

REPO = Path(__file__).resolve().parents[1]
PAIRS = (("market", 137, "steward"), ("consumer", 223, "everyday"))
DURATION = 7200
INTERVAL = 600


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {str(p.relative_to(REPO)): fingerprint(p) for folder in ("worlds", "experiments", "evals")
            for p in sorted((REPO / folder).glob("*.py"))}


def calendar(pack, seed):
    actors = scenarios.actors(pack)
    return [{"offset_seconds": period * 1200,
             "shared": scenarios.shared_circumstance(seed, period),
             "circumstances": {a: scenarios.circumstance(seed, a, period)
                               for a, info in actors.items() if info["role"] == "counterpart"}}
            for period in range(1, 6)]


def select_fresh_subject(world, selected):
    """Do not advertise unlaunched companies as available trading partners."""
    if (world.s.root / "deployment.json").exists():
        raise ValueError("subject selection is only allowed before fresh deployment")
    with world.s.transaction() as db:
        actors = {row["id"]: json.loads(row["body"]) for row in db.execute("SELECT id,body FROM actors")}
        if selected not in actors or actors[selected]["role"] != "subject":
            raise ValueError("known subject required")
        if db.execute("SELECT count(*) FROM calls").fetchone()[0] or db.execute("SELECT count(*) FROM receipts").fetchone()[0]:
            raise ValueError("selection cannot change an already used world")
        omitted = [actor for actor, info in actors.items() if info["role"] == "subject" and actor != selected]
        opening = world.s.meta(db, "opening_balances")
        for actor in omitted:
            db.execute("DELETE FROM records WHERE owner=?", (actor,))
            db.execute("DELETE FROM balances WHERE account=?", (actor,))
            db.execute("DELETE FROM actors WHERE id=?", (actor,))
            opening.pop(actor, None)
        world.s.meta(db, "opening_balances", opening)
        cfg = world.s.meta(db, "config")
        cfg["scenario_hash"] = digest({a: info for a, info in actors.items() if a not in omitted})
        world.s.meta(db, "config", cfg)
        world.s.event(db, "_operator", "fresh_subject_selection", {"selected": selected, "omitted": omitted,
            "reason": "Only launched companies are publicly available; no phantom unresponsive suppliers"})


def prepare(root, baseline, candidate):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("fresh cohort directory required; no implicit relaunch")
    images = {arm: command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
              for arm, image in (("baseline", baseline), ("candidate", candidate))}
    if images["baseline"] == images["candidate"]:
        raise ValueError("comparison requires distinct pinned images")
    root.mkdir(parents=True, mode=0o700)
    started = time.time()
    manifest = {"status": "prepared", "started": started, "cutoff": started + DURATION,
        "images": images, "worlds": {}, "arms": {}, "source_revision": command(["git", "rev-parse", "HEAD"], cwd=REPO),
        "controller_source_hashes": source_hashes(), "model": "gpt-5.6-terra", "effort": "medium",
        "auth": "ChatGPT subscription, fail closed; no API fallback", "capture_interval_seconds": INTERVAL,
        "ordinary_exposure_schedule": organizational_exposure.schedule(),
        "exposure_basis": "Declared operator-scripted ordinary counterpart requests, not spontaneous demand or adoption",
        "limits": {"duration_seconds": DURATION, "subject_starts_per_hour": 20, "subject_count": 4,
            "subject_concurrency": 1, "subject_deadline_seconds": 480,
            "counterpart_calls_per_actor_hour": 4, "counterpart_response_reserve": 1,
            "maximum_subject_phase_calls_hour": 160, "maximum_counterpart_calls_hour": 96,
            "maximum_ordinary_total_calls_hour": 256, "shared_call_cap_hour": 320},
        "design": "Two isolated matched domains, one fresh self per arm; only embedded practice differs. Counterpart decisions remain stochastic.",
        "limitations": ["Two domain pairs are exploratory, not replicated statistical evidence.",
            "Common wall-clock cutoff includes startup loss; no extension to recover lost time.",
            "20-minute operating periods accelerate new occasions, not model execution or delivery deadlines.",
            "Public research is read-only; simulated payments/usage are not external demand.",
            "Count actual customer outcomes and net saved attention, not programs, nodes, or activity."]}
    for pack, seed, actor in PAIRS:
        for arm in ("baseline", "candidate"):
            name = pack + "-" + arm
            world = World(root / name)
            world.create(pack, seed, hours=2)
            select_fresh_subject(world, actor)
            with world.s.transaction() as db:
                cfg = world.s.meta(db, "config")
                cfg.update(started=started, cutoff=manifest["cutoff"], period_seconds=1200,
                    baseline_starts=20, maximum_starts=20, activation_deadline=480,
                    counterpart_calls_per_hour=4, counterpart_response_reserve=1,
                    calls_per_hour=100, concurrency=3, shared_concurrency=8, shared_calls_per_hour=320,
                    staff_per_period=24, experiment="organizational-tendency-v1")
                world.s.meta(db, "config", cfg)
                for index, project in enumerate(world.s.rows(db, "project")):
                    world.s.revise(db, project, staff_remaining=24)
                    continuation = world.s.get(db, "continuation:" + project["owner"])
                    world.s.revise(db, continuation, next_at=started+45+index*20,
                                   reason="Initial ordinary review; incumbent operation remains available")
            manifest["worlds"][name] = str(world.s.root)
            manifest["arms"][name] = {"arm": arm, "pack": pack, "seed": seed, "subject": actor,
                "image": images[arm], "scenario_hash": cfg["scenario_hash"],
                "calendar": calendar(pack, seed), "budget_file": str(root / "world-budget" / "calls.jsonl")}
    save(root / "cohort.json", manifest)
    return manifest


def validate_controller(manifest):
    if source_hashes() != manifest["controller_source_hashes"]:
        raise ValueError("controller source changed since manifest; run from the pinned checkout")
    if time.time() >= manifest["cutoff"]:
        raise ValueError("experiment already expired")


def capture_seed(root, name, actor, instance, image):
    """Executed after complete seeding/configuration, before first process start."""
    root, instance = Path(root), Path(instance)
    manifest = json.loads((root / "cohort.json").read_text())
    arm = manifest["arms"][name]
    if actor != arm["subject"] or image != arm["image"]:
        raise ValueError("seed provenance does not match arm")
    state = json.loads((instance / ".concorde2/state.json").read_text())
    cfg = state["config"]
    if state.get("activations") or cfg.get("model") != "gpt-5.6-terra" or cfg.get("effort") != "medium":
        raise ValueError("fresh Terra medium state required")
    if cfg.get("starts_per_hour") != 20 or cfg.get("concurrency") != 1 or cfg.get("deadline_seconds") != 480:
        raise ValueError("unexpected subject admission limits")
    cutoff = dt.datetime.fromisoformat(cfg.get("freeze_at", "").replace("Z", "+00:00")).timestamp()
    if abs(cutoff - manifest["cutoff"]) > 0.001 or time.time() >= cutoff:
        raise ValueError("subject cutoff does not match the live experiment window")
    target = root / "seeds" / name
    target.mkdir(parents=True, mode=0o700)
    files = {}
    for relative in (".concorde2/state.json", ".concorde2/events.jsonl", "brain.json"):
        source = instance / relative
        # Preserve exact bytes, including initial provenance, without credentials.
        data = source.read_bytes()
        destination = target / source.name
        with destination.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        destination.chmod(0o600)
        files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    save(target / "manifest.json", {"captured": time.time(), "subject": actor, "image": image,
        "startup_loss_seconds": time.time() - manifest["started"], "sequence": state["seq"], "files": files})


def worker(root, name):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    validate_controller(manifest)
    arm = manifest["arms"][name]
    os.environ["WORLD_BUDGET_FILE"] = arm["budget_file"]
    campaign.run(World(Path(manifest["worlds"][name])), image=arm["image"], subjects=[arm["subject"]],
                 before_subject_start=lambda actor, instance, image: capture_seed(root, name, actor, instance, image))


def capture(root, number, scheduled):
    snapshot = forensics.snapshot(root, root / "audit", number, scheduled=scheduled)
    forensics.digest_round(root / "audit", number)
    delta = forensics.compare_round(root / "audit", number)
    save(root / f"review-{number:02d}.json", {"scheduled": scheduled, "captured": time.time(),
        "capture_errors": snapshot["errors"], "delta": delta,
        "judgment": "Human review pending; counts do not establish business competence."})
    with (root / "running-log.jsonl").open("a") as stream:
        stream.write(json.dumps({"round": number, "scheduled": scheduled, "captured": time.time(),
            "capture_errors": snapshot["errors"]}) + "\n")
        stream.flush(); os.fsync(stream.fileno())


def run(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    if manifest["status"] != "prepared":
        raise ValueError("fresh prepared cohort required; no restart")
    validate_controller(manifest)
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children, streams, errors = {}, [], []
    exposure_retry = {}
    manifest.update(status="running", controller_pid=os.getpid())
    save(root / "cohort.json", manifest)
    number = 0
    next_at = manifest["started"] + INTERVAL
    try:
        for name in manifest["worlds"]:
            log = (root / (name + ".controller.log")).open("x"); streams.append(log)
            children[name] = subprocess.Popen([sys.executable, "-m", "experiments.organizational_lives",
                "worker", str(root), "--name", name], cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        manifest["processes"] = {name: child.pid for name, child in children.items()}
        save(root / "cohort.json", manifest)
        while not stopping and time.time() < manifest["cutoff"]:
            failed = {name: child.poll() for name, child in children.items() if child.poll() is not None}
            if failed:
                raise RuntimeError("world controller exited before common cutoff: " + str(failed))
            for name, path in manifest["worlds"].items():
                if time.time() < exposure_retry.get(name, 0):
                    continue
                try:
                    organizational_exposure.tick(World(Path(path)), manifest["arms"][name]["subject"],
                                                 time.time() - manifest["started"])
                except Exception as error:
                    diagnostic = {"exposure": name, "at": time.time(), "error": str(error)}
                    errors.append(diagnostic)
                    with (root / "running-log.jsonl").open("a") as stream:
                        stream.write(json.dumps(diagnostic) + "\n"); stream.flush(); os.fsync(stream.fileno())
                    exposure_retry[name] = time.time() + 30
            if time.time() >= next_at:
                try:
                    capture(root, number, next_at)
                except Exception as error:
                    errors.append({"capture": number, "error": str(error)})
                number += 1
                next_at = manifest["started"] + (number + 1) * INTERVAL
            time.sleep(2)
    except BaseException as error:
        errors.append({"controller": str(error)})
        raise
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        # Freeze before and after dispatchers stop: prevents late creation races.
        for final_pass in (False, True):
            for name, path in manifest["worlds"].items():
                os.environ["WORLD_BUDGET_FILE"] = manifest["arms"][name]["budget_file"]
                try:
                    campaign.freeze(World(Path(path)))
                except Exception as error:
                    errors.append({"freeze": name, "final_pass": final_pass, "error": str(error)})
            if not final_pass:
                for child in children.values():
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait(timeout=15)
        manifest.update(status="frozen", finished=time.time(), errors=errors,
            exit_codes={name: child.poll() for name, child in children.items()})
        save(root / "cohort.json", manifest)
        try:
            capture(root, number, manifest["cutoff"])
            closure = forensics.closure(root, root / "audit")
            manifest["closure_errors"] = closure["errors"]
        except Exception as error:
            manifest["closure_errors"] = [{"error": str(error)}]
        if manifest.get("closure_errors") or any(e.get("final_pass") for e in errors):
            manifest["status"] = "closed_requires_review"
        save(root / "cohort.json", manifest)
        for stream in streams:
            stream.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "worker"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--baseline-image")
    parser.add_argument("--candidate-image")
    parser.add_argument("--name")
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.baseline_image or not args.candidate_image:
            parser.error("prepare requires both images")
        result = prepare(args.root, args.baseline_image, args.candidate_image)
        print(json.dumps(result, indent=2))
    elif args.action == "worker":
        if not args.name:
            parser.error("worker requires --name")
        worker(args.root, args.name)
    else:
        run(args.root)


if __name__ == "__main__":
    main()

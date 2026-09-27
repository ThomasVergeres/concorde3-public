"""Reproducible untouched-C3 formation baseline, using qualified World probes.

Promotes the earlier private runner to a bounded, versioned entry point. No
practice edit, forced purchase, retry, or semantic success inferred from a refund.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from experiments.closure_continuation import account
from experiments.reporting_dependency import REPO, SUPPLEMENTS, command
from worlds import campaign, forensics, formation_probe, replay
from worlds.engine import World
from worlds.forensics import write_json

PROFILES = {
    "luna": ("gpt-5.6-luna", "xhigh", .02),
    "terra": ("gpt-5.6-terra", "medium", .15),
}


def reservation(observed, supplements, variants, profile="luna", ceiling=3.15):
    if profile not in PROFILES or type(ceiling) not in (int, float) or ceiling not in (3.15, 3.5):
        raise ValueError("declared model profile and owner-approved ceiling required")
    if not variants or len(set(variants)) != len(variants) or set(variants) - set(formation_probe.VARIANTS):
        raise ValueError("unique declared provider conditions required")
    if observed["accounting_errors"] or observed["shot_violations"]:
        raise ValueError("close/account previous trials before reserving another baseline")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (observed["known_multiple"], supplements)):
        raise ValueError("nonnegative finite measured accounting required")
    reserve = PROFILES[profile][2] * len(variants)
    planning = observed["known_multiple"] + supplements + .50 + reserve
    if planning > ceiling:
        raise ValueError("new baseline exceeds approved shared campaign planning ceiling")
    return {"approved_ceiling_B": ceiling, "unknown_allowance_B": .50,
            "panel_reserve_B": reserve, "partial_supplement_B": supplements, "planning_B": planning}


def baseline_account(root, additions=()):
    closure = [root / "closure-continuation/baseline-01" / v for v in ("opportunity", "quiet")]
    # These named panels are part of this campaign even when a later caller does
    # not explicitly supply them. collect deduplicates records by canonical ID.
    formation = [p for name in ("formation-current-baseline", "formation-terra-baseline", "formation-scope-candidate")
                 for p in (root / name / v for v in formation_probe.VARIANTS)
                 if (p / "world.sqlite").is_file()]
    return account(root, closure + formation + list(additions))


def run(root, output, snapshot, fixture, binary, image, variants, profile="luna", ceiling=3.15):
    root, output, snapshot = (Path(p).resolve() for p in (root, output, snapshot))
    variants = variants.split(",")
    if output.exists(): raise ValueError("fresh output required; no implicit repeat")
    if command(["git", "status", "--porcelain"]): raise ValueError("commit protocol/runner before dispatch")
    if command(["docker", "ps", "-q"]): raise ValueError("finish existing trials before this reservation")
    observed = baseline_account(root)
    extra = sum(json.loads((root / p).read_text())["additional_observed_B"] for p in SUPPLEMENTS)
    reserved = reservation(observed, extra, variants, profile, ceiling)
    model, effort, _ = PROFILES[profile]
    source = replay.load(snapshot)
    original = Path(source["source"])
    before = {row["path"]: hashlib.sha256((original / row["path"]).read_bytes()).hexdigest() for row in source["files"]}
    if any(before[row["path"]] != row["sha256"] for row in source["files"]): raise ValueError("source changed")
    image = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    output.mkdir(parents=True, mode=0o700)
    meta = {"status": "preparing", "started": time.time(), "controller_pid": os.getpid(),
        "source_revision": command(["git", "rev-parse", "HEAD"]), "image": image,
        "source_manifest": hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(Path(fixture).read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model": model, "effort": effort, "draw": 0, "reservation": reserved,
        "assistance": "Explicit finite operator reentry of dormant copied purpose, not organic receiving; no practice refresh",
        "worlds": {}, "processes": [], "errors": []}
    write_json(output / "pre-account.json", observed)
    write_json(output / "cohort.json", meta)
    children, logs = [], []
    try:
        for variant in variants:
            dest = output / variant
            # Include a partially prepared destination in cleanup inventory.
            meta["worlds"][variant] = str(dest); write_json(output / "cohort.json", meta)
            fork = replay.fork(snapshot, dest, fixture, hours=formation_probe.VARIANTS[variant] / 3600,
                model=model, effort=effort, starts=2, acknowledge_dormancy=True)
            world = World(dest)
            with world.s.transaction() as db:
                cfg = world.s.meta(db, "config"); cfg["counterpart_mode"] = "scripted"
                world.s.meta(db, "config", cfg)
            command([binary, "configure", dest / "subjects/everyday", json.dumps({
                "freeze_at": dt.datetime.fromtimestamp(fork["started"] + 600, dt.UTC).isoformat(), "deadline_seconds": 300})])
            formation_probe.prepare(world, variant)
            command([binary, "notify", dest / "subjects/everyday", "purpose", "operator-finite-reentry",
                "Operator resumes this isolated copy of the existing undertaking for a finite continuation."])
            prefix = "c3-world-" + campaign.world_id(dest)
            at = dt.datetime.fromtimestamp(fork["started"] + 603, dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            command(["sudo", "-n", "systemd-run", "--quiet", "--unit", prefix + "-subject-stop", "--on-calendar", at,
                "--timer-property=AccuracySec=1s", "--property=User=codex", "/usr/bin/docker", "stop", "-t", "3", prefix + "-everyday"])
        for variant, dest in meta["worlds"].items():
            for role, args in (("observer", [sys.executable, "-m", "worlds.formation_probe", dest]),
                ("runtime", [sys.executable, "-m", "worlds.cli", "run", dest, "--image", image, "--subjects", "everyday"])):
                log = (output / (variant + "-" + role + ".log")).open("x"); logs.append(log)
                child = subprocess.Popen(args, cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
                children.append(child); meta["processes"].append({"variant": variant, "role": role, "pid": child.pid})
        meta.update(status="running", dispatched_at=time.time()); write_json(output / "cohort.json", meta)
        print(json.dumps({"running": meta["processes"], "reservation": reserved}), flush=True)
        first = min(json.loads((Path(p) / "fork.json").read_text())["started"] for p in meta["worlds"].values())
        offsets = [20, 300, 590, 610, 730, 785, 1200, 1810]
        number = 0
        while any(p.poll() is None for p in children) or number < len(offsets):
            if any(p.poll() not in (None, 0) for p in children): raise RuntimeError("child failed; stop incomplete comparison, no retry")
            if number < len(offsets) and time.time() >= first + offsets[number]:
                captured = forensics.snapshot(output, output / "audit", number, scheduled=first + offsets[number])
                forensics.digest_round(output / "audit", number)
                write_json(output / f"counterparts-round-{number:02d}.json", {
                    v: json.loads((Path(p) / formation_probe.FILE).read_text()) for v, p in meta["worlds"].items()})
                print(json.dumps({"round": number, "at": time.time(), "errors": captured["errors"]}), flush=True)
                number += 1
            if time.time() > first + 1890: raise RuntimeError("finite formation grace exceeded")
            time.sleep(1)
    except BaseException as error:
        meta["errors"].append({"error": str(error), "stderr": getattr(error, "stderr", None)})
        raise
    finally:
        for variant, dest in meta["worlds"].items():
            if (Path(dest) / "world.sqlite").exists():
                try: campaign.freeze(World(Path(dest)))
                except Exception as error: meta["errors"].append({"freeze": variant, "error": str(error)})
            for state in (Path(dest) / "subjects").glob("*/.concorde2/state.json"):
                try: command([binary, "freeze", state.parent.parent])
                except Exception as error: meta["errors"].append({"canonical_freeze": variant, "error": str(error)})
        for child in children:
            if child.poll() is None: child.terminate()
            try: child.wait(timeout=30)
            except subprocess.TimeoutExpired: meta["errors"].append({"live_child": child.pid})
        for log in logs: log.close()
        meta.update(status="closed_unverified", finished=time.time(), exit_codes=[p.poll() for p in children])
        meta["source_files_unchanged"] = all(hashlib.sha256((original / p).read_bytes()).hexdigest() == h for p, h in before.items())
        write_json(output / "cohort.json", meta)
        write_json(output / "final-account.json", baseline_account(root, [Path(p) for p in meta["worlds"].values() if (Path(p) / "world.sqlite").exists()]))
        print(json.dumps({"finished": meta["finished"], "exit_codes": meta["exit_codes"], "errors": meta["errors"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "output", "snapshot", "fixture", "binary", "image"): parser.add_argument("--" + key, required=True)
    parser.add_argument("--variants", default="adequate-provider,short-provider")
    parser.add_argument("--profile", choices=PROFILES, default="luna")
    parser.add_argument("--ceiling", type=float, choices=(3.15, 3.5), default=3.15,
                        help="3.5 is the owner's September 11 approved shared campaign ceiling; no reset")
    run(**vars(parser.parse_args()))

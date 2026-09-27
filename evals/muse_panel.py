"""Archived September 11 Muse comparison; not the current readiness portfolio.

The historical 24-cell and active 56-cell panels overlap in eight cells. Reuse
those observations in both reports rather than silently granting a third draw.
All trials are newly dispatched against one pinned implementation; historical
means the registered case selection, not identical historical model or image.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys

from .lab import ROOT, save
from .model_transport import MUSE_PROFILE

# Freeze the actual historical selection. Reading today's portfolio here would
# rewrite old report expectations and produce mismatched groups/shot counts.
ARCHIVED_ACTIVE = ("RG01", "CF01", "AR06", "AR07", "AR08", "HD01", "UX01",
                   "AR05", "AR04", "AU02", "AR01", "AR02", "AR03", "LR01")
ARCHIVED_LONG = {"HD01", "UX01", "AR05", "AR04", "AU02", "AR01", "AR02", "AR03"}


def cells():
    baseline = json.loads((ROOT / "evals/readiness-data/cost-baseline.json").read_text())
    historical = {(r["case"], r["variant"], r["world_seed"], 0) for r in baseline["rows"]}
    active = {(family, variant, 1, draw) for family in ARCHIVED_ACTIVE
              for variant in ("challenge", "control") for draw in range(2)}
    return [{"case": case, "variant": variant, "world_seed": seed, "draw": draw,
             "panels": (["historical24"] if (case, variant, seed, draw) in historical else []) +
                       (["active"] if (case, variant, seed, draw) in active else [])}
            for case, variant, seed, draw in sorted(historical | active)]


def groups():
    result = []
    for case in sorted({c["case"] for c in cells()}):
        long = case in ARCHIVED_LONG
        wall, starts = (900, 4) if case == "AR04" else (600, 4) if long else (360, 1)
        # One command covers both active draws; an additional command adds only
        # historical seeds 2/3. Seed1/draw0 is shared, not re-run.
        result.append({"case": case, "worlds": "1", "draws": 2, "wall": wall, "starts": starts,
                       "name": case + "-active"})
        if case in {"AR05", "AR06", "AR07", "AR08"}:
            result.append({"case": case, "worlds": "2,3", "draws": 1, "wall": wall, "starts": starts,
                           "name": case + "-historical-extra"})
    return sorted(result, key=lambda g: (-g["wall"], g["name"]))


def commands(args):
    result = []
    for group in groups():
        command = [sys.executable, "-m", "evals.lab", "run", "--output", str(Path(args.output) / group["name"]),
                   "--fixture", args.fixture, "--image", args.image, "--models", MUSE_PROFILE,
                   "--api-broker", args.api_broker, "--api-broker-token", args.api_broker_token,
                   "--api-adapter", args.api_adapter, "--cases", group["case"], "--variants", "challenge,control",
                   "--worlds", group["worlds"], "--draws", str(group["draws"]), "--entry", "episode",
                   "--starts", str(group["starts"]), "--wall", str(group["wall"]), "--deadline", "240",
                   "--workers", "4", "--pool-cap", "1000", "--ledger", args.ledger]
        result.append({**group, "command": command})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("output", "fixture", "image", "api-broker", "api-broker-token", "api-adapter"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--ledger", default=os.environ.get('CONCORDE_LAB_LEDGER',str(Path.home()/'.local/share/concorde/behavioral-lab/admissions.jsonl')))
    parser.add_argument("--execute", action="store_true", help="Dispatch paid API trials; default is planning only")
    parser.add_argument("--historical-20260911", action="store_true",
                        help="Explicitly reproduce the archived 72-cell selection, including now-demoted cases")
    parser.add_argument("--drivers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.execute and not args.historical_20260911:
        parser.error("archived comparison: --execute also requires --historical-20260911; use evals.readiness for the current portfolio")
    if not 1 <= args.drivers <= 4: parser.error("drivers must be1–4; shared16episode slots remain authoritative")
    plan = {"selection": "archived-20260911-not-current-portfolio", "model_profile": MUSE_PROFILE, "cells": cells(), "commands": commands(args),
            "counts": {"historical24": 24, "active": 56, "shared": 8, "unique_dispatches": 72},
            "rules": "Fixed planned shots; all outcomes retained. Shared cells are not independent evidence. No model retry-until-green or automatic LLM judge."}
    if not args.execute:
        print(json.dumps(plan, indent=2)); return
    output = Path(args.output)
    if output.exists() and any(output.iterdir()): parser.error("output must be new/empty")
    save(output / "comparison-plan.json", plan)
    def run(group):
        name = group["name"]
        with (output / (name + ".stdout")).open("w") as stdout, (output / (name + ".stderr")).open("w") as stderr:
            code = subprocess.run(group["command"], stdout=stdout, stderr=stderr).returncode
        return {"group": name, "exit_code": code}
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.drivers) as pool:
        jobs = [pool.submit(run, group) for group in plan["commands"]]
        for future in concurrent.futures.as_completed(jobs):
            result = future.result(); results.append(result)
            save(output / "driver-results.json", results)
            print(json.dumps(result), flush=True)
    raise SystemExit(1 if any(r["exit_code"] for r in results) else 0)


if __name__ == "__main__": main()

"""Finite prospective commitment probe from one immutable lived origin.

Only new scoped prefixes are launched/stopped. Existing comparison worlds remain
untouched. Run from a pinned controller checkout after no-model qualification.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from evals.campaign_account import collect
from evals.lab import command
from experiments.formation_baseline import baseline_account
from experiments.reporting_dependency import SUPPLEMENTS
from worlds import campaign, forensics, offer_window_probe as probe, replay
from worlds.engine import World
from worlds.forensics import write_json

REPO = Path(__file__).resolve().parents[1]


def reservation(original, supplements, migration):
    for value in (original["known_multiple"], supplements, migration["known_multiple"]):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("finite nonnegative measured accounting required")
    for report in (original, migration):
        if report["accounting_errors"] or report["shot_violations"]:
            raise ValueError("close previous experiments before using their actual accounting")
    if migration["unknown"]:
        raise ValueError("new migration usage must be known before releasing its reservation")
    used = migration["known_multiple"]
    remaining = .20 - used
    if used < 0 or remaining < .17:
        raise ValueError("amendment would exceed the originally approved additional envelope")
    planning = original["known_multiple"] + supplements + .50 + used + .17
    if planning > 3.5:
        raise ValueError("amendment would exceed unchanged campaign ceiling")
    return {"approved_ceiling_B": 3.5, "old_unknown_allowance_B": .50,
            "original_known_B": original["known_multiple"], "partial_supplement_B": supplements,
            "migration_actual_B": used, "additional_envelope_B": .20,
            "pair_reserve_B": .08, "conditional_reserve_B": .05, "new_unknown_reserve_B": .04,
            "planning_B": planning, "reason": "Explicit reallocation of unused RG02 envelope, no reset."}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finish_owned(meta, children, binary):
    """Stop controller groups; the caller has already attempted an initial freeze."""
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)
            meta["errors"].append({"forced_child_stop": child.pid})
    meta["closure_inventory"] = {}
    for variant, location in meta["worlds"].items():
        location = Path(location)
        if not (location / "world.sqlite").exists():
            continue
        try:
            world = World(location)
            # A controller may have created a labeled container after the first
            # freeze. All controller groups are now reaped; close that race.
            campaign.freeze(world)
            states = {}
            for path in (location / "subjects").glob("*/.concorde2/state.json"):
                command([binary, "freeze", path.parent.parent])
                state = json.loads(path.read_text())
                states[path.parent.parent.name] = {"mode": state["mode"], "running_activations":
                    sum(a.get("status") == "running" for a in state["activations"].values())}
            with world.s.transaction() as db:
                frozen = world.s.meta(db, "frozen")
                pending = db.execute("SELECT count(*) FROM calls WHERE status IN ('reserved','running','uncertain')").fetchone()[0]
            running = replay.running(location)
            verified = bool(frozen) and not pending and not running and set(states) == set(meta.get("expected_subjects", ["everyday"])) and all(
                s["mode"] == "frozen" and s["running_activations"] == 0 for s in states.values())
            meta["closure_inventory"][variant] = {"verified": verified, "world_frozen": frozen,
                "pending_calls": pending, "running_labeled_containers": running, "states": states}
            if not verified:
                meta["errors"].append({"final_closure": variant, "error": "owned execution remains unresolved"})
        except Exception as error:
            meta["closure_inventory"][variant] = {"verified": False, "error": str(error)}
            meta["errors"].append({"final_freeze": variant, "error": str(error)})


def qualify(snapshot, output, fixture, binary):
    """No-model rehearsal of the actual donor/core/probe boundary."""
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("fresh qualification output required")
    source = replay.load(snapshot)
    original = Path(source["source"])
    before = {row["path"]: sha(original / row["path"]) for row in source["files"]}
    if any(before[row["path"]] != row["sha256"] for row in source["files"]):
        raise ValueError("source changed")
    output.mkdir(parents=True, mode=0o700)
    result = {"kind": "no-model-lived-probe-qualification", "source_manifest": sha(snapshot / "manifest.json"),
              "fixture_sha256": sha(fixture), "binary_sha256": sha(binary), "conditions": {}}
    for variant in probe.DELAYS:
        destination = output / variant
        fork = replay.fork(snapshot, destination, fixture, hours=probe.PROVIDER_SECONDS / 3600,
                           model="gpt-5.6-luna", effort="xhigh", starts=2, acknowledge_dormancy=True)
        world = World(destination)
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg["counterpart_mode"] = "scripted"
            world.s.meta(db, "config", cfg)
        command([binary, "configure", destination / "subjects/everyday", json.dumps({
            "freeze_at": dt.datetime.fromtimestamp(fork["started"] + 600, dt.UTC).isoformat(), "deadline_seconds": 300})])
        record = probe.prepare(world, variant)
        command([fixture, "validate", destination / "subjects/everyday"])
        help_view = world.view("everyday", "help")
        assert "expires_at" in help_view["schema"]["offer"] and "withdraw_offer" in help_view["schemas"]
        result["conditions"][variant] = {"prepared": True, "native_state_valid": True,
            "purchase_at": record["purchase_at"], "provider_cutoff": record["provider_cutoff"],
            "source_state_sha256": record["initial_state_sha256"]}
        world.freeze()
        command([binary, "freeze", destination / "subjects/everyday"])
    result["source_files_unchanged"] = all(sha(original / path) == old for path, old in before.items())
    write_json(output / "qualification.json", result)
    return result


def run(root, migration_root, output, snapshot, fixture, binary, image):
    root, migration_root, output, snapshot = map(lambda p: Path(p).resolve(), (root, migration_root, output, snapshot))
    if output.exists():
        raise ValueError("fresh output required; no implicit replay")
    if command(["git", "status", "--porcelain"], cwd=REPO):
        raise ValueError("commit and pin the runner/protocol before dispatch")
    original_account = baseline_account(root)
    migration_account = collect(labs=[migration_root / "baseline-main", migration_root / "baseline-heldout"])
    supplements = sum(json.loads((root / p).read_text())["additional_observed_B"] for p in SUPPLEMENTS)
    reserve = reservation(original_account, supplements, migration_account)
    source = replay.load(snapshot)
    original = Path(source["source"])
    if replay.running(original):
        raise ValueError("source world is not closed")
    source_hashes = {row["path"]: sha(original / row["path"]) for row in source["files"]}
    if any(source_hashes[row["path"]] != row["sha256"] for row in source["files"]):
        raise ValueError("source changed after qualification")
    resolved_image = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    output.mkdir(parents=True, mode=0o700)
    meta = {"status": "preparing", "started": time.time(), "controller_pid": os.getpid(),
        "controller_source": str(REPO), "source_revision": command(["git", "rev-parse", "HEAD"], cwd=REPO),
        "runner_sha256": sha(__file__), "image": resolved_image, "fixture_sha256": sha(fixture),
        "binary_sha256": sha(binary), "source_manifest": sha(snapshot / "manifest.json"),
        "model": "gpt-5.6-luna", "effort": "xhigh", "draw": 0, "declared_scope": "sim_tests", "expected_subjects": ["everyday"],
        "reservation": reserve, "worlds": {}, "processes": [], "errors": [],
        "unrelated_containers_at_start": command(["docker", "ps", "--format", "{{.Names}} "]).splitlines(),
        "assistance": "Finite operator reentry of an isolated dormant lived copy; no product/kit edits; ordinary customer inquiry.",
        "limits": {"starts_per_subject": 2, "cognition_seconds": 600, "provider_seconds": probe.PROVIDER_SECONDS,
                   "observation_seconds": probe.OBSERVE_SECONDS, "shared_pool": 200}}
    write_json(output / "pre-account.json", {"original": original_account, "migration": migration_account})
    write_json(output / "cohort.json", meta)
    children, logs = [], []
    def interrupted(signum, frame):
        raise RuntimeError("finite controller interrupted by signal " + str(signum))
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    try:
        for variant in probe.DELAYS:
            location = output / variant
            meta["worlds"][variant] = str(location)
            write_json(output / "cohort.json", meta)
            fork = replay.fork(snapshot, location, fixture, hours=probe.PROVIDER_SECONDS / 3600,
                               model="gpt-5.6-luna", effort="xhigh", starts=2, acknowledge_dormancy=True)
            world = World(location)
            with world.s.transaction() as db:
                cfg = world.s.meta(db, "config")
                cfg.update(counterpart_mode="scripted", shared_calls_per_hour=200, shared_concurrency=8)
                world.s.meta(db, "config", cfg)
            command([binary, "configure", location / "subjects/everyday", json.dumps({
                "freeze_at": dt.datetime.fromtimestamp(fork["started"] + 600, dt.UTC).isoformat(),
                "deadline_seconds": 300})])
            probe.prepare(world, variant)
            command([binary, "notify", location / "subjects/everyday", "purpose", "operator-finite-reentry",
                     "Operator resumes this isolated copy of the existing undertaking for a finite continuation."])
            prefix = "c3-world-" + campaign.world_id(location)
            at = dt.datetime.fromtimestamp(fork["started"] + 603, dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            command(["sudo", "-n", "systemd-run", "--quiet", "--unit", prefix + "-subject-stop", "--on-calendar", at,
                     "--timer-property=AccuracySec=1s", "--property=User=codex", "/usr/bin/docker", "stop", "-t", "3", prefix + "-everyday"])
        for variant, location in meta["worlds"].items():
            for role, args in (
                ("observer", [sys.executable, "-m", "worlds.offer_window_probe", location]),
                ("runtime", [sys.executable, "-m", "worlds.cli", "run", location, "--image", resolved_image, "--subjects", "everyday"]),
            ):
                log = (output / f"{variant}-{role}.log").open("x")
                logs.append(log)
                child = subprocess.Popen(args, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                children.append(child)
                meta["processes"].append({"variant": variant, "role": role, "pid": child.pid})
        meta.update(status="running", dispatched_at=time.time())
        write_json(output / "cohort.json", meta)
        print(json.dumps({"running": meta["processes"], "reservation": reserve}), flush=True)
        origin = min(json.loads((Path(p) / "fork.json").read_text())["started"] for p in meta["worlds"].values())
        offsets = (20, 190, 300, 590, 610, 790, 850, 1325, 1440, 1480)
        number = 0
        while any(child.poll() is None for child in children) or number < len(offsets):
            if any(child.poll() not in (None, 0) for child in children):
                raise RuntimeError("child failed; retain incomplete trial, no retry")
            if number < len(offsets) and time.time() >= origin + offsets[number]:
                snapshot_record = forensics.snapshot(output, output / "audit", number, scheduled=origin + offsets[number])
                forensics.digest_round(output / "audit", number)
                write_json(output / f"probe-round-{number:02d}.json", {
                    variant: json.loads((Path(p) / probe.FILE).read_text()) for variant, p in meta["worlds"].items()})
                print(json.dumps({"round": number, "at": time.time(), "errors": snapshot_record["errors"]}), flush=True)
                number += 1
            if time.time() > origin + 1560:
                raise RuntimeError("finite observation grace exceeded")
            time.sleep(1)
    except BaseException as error:
        meta["errors"].append({"error": str(error), "stderr": getattr(error, "stderr", None)})
        raise
    finally:
        for variant, location in meta["worlds"].items():
            if (Path(location) / "world.sqlite").exists():
                try:
                    campaign.freeze(World(Path(location)))
                except Exception as error:
                    meta["errors"].append({"freeze": variant, "error": str(error)})
            for state in (Path(location) / "subjects").glob("*/.concorde2/state.json"):
                try:
                    command([binary, "freeze", state.parent.parent])
                except Exception as error:
                    meta["errors"].append({"canonical_freeze": variant, "error": str(error)})
        finish_owned(meta, children, binary)
        for log in logs:
            log.close()
        verified = bool(meta["closure_inventory"]) and all(row["verified"] for row in meta["closure_inventory"].values())
        meta.update(status="frozen" if verified else "closed_requires_review", finished=time.time(), exit_codes=[child.poll() for child in children])
        meta["source_files_unchanged"] = all(sha(original / path) == old for path, old in source_hashes.items())
        write_json(output / "cohort.json", meta)
        locations = [Path(path) for path in meta["worlds"].values() if (Path(path) / "world.sqlite").exists()]
        if locations:
            write_json(output / "probe-account.json", collect(worlds=locations))
        print(json.dumps({"finished": meta["finished"], "errors": meta["errors"], "exit_codes": meta["exit_codes"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "migration-root", "output", "snapshot", "fixture", "binary", "image"):
        parser.add_argument("--" + key, required=True)
    run(**vars(parser.parse_args()))

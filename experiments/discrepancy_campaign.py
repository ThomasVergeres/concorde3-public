"""One finite 12-self discrepancy-driven improvement campaign.

Runtime evidence is private and must live outside Git.  This controller never
relaunches an existing campaign and never mutates C3's normal operating defaults.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from evals.lab import command, save
from evals import campaign_account
from experiments.organizational_lives import select_fresh_subject
from worlds import campaign, forensics
from worlds.engine import World
from .discrepancy_engine import (REVIEW_PROMPT, CURATOR_PROMPT, build_packet,
                                 validate_review, validate_curation, usage_totals,
                                 targeted_case_packet, review_response_schema, curation_response_schema,
                                 supplied_activation_ids)
from .discrepancy_store import DiscrepancyStore, encoded, case_scope_matches
from .discrepancy_transport import MetaDriver, stop_active


REPO = Path(__file__).resolve().parents[1]
DURATION = 10800
INTERVAL = 600
SUBJECT_MODEL = "gpt-5.6-luna"
META_MODEL = "gpt-5.6-sol"
EFFORT = "xhigh"
META_EFFORT = "high"
ARMS = (
    ("market-reach-311", "market", 311, "reach"),
    ("market-steward-312", "market", 312, "steward"),
    ("market-frontier-313", "market", 313, "frontier"),
    ("market-steward-314", "market", 314, "steward"),
    ("consumer-everyday-321", "consumer", 321, "everyday"),
    ("consumer-everyday-322", "consumer", 322, "everyday"),
    ("consumer-everyday-323", "consumer", 323, "everyday"),
    ("research-331", "research", 331, "researcher"),
    ("research-332", "research", 332, "researcher"),
    ("research-333", "research", 333, "researcher"),
    ("coordination-341", "coordination", 341, "coordinator"),
    ("coordination-342", "coordination", 342, "coordinator"),
)


def local_call_allowance(pack, counterpart_limit, starts=12):
    """A world cap counts phase calls, not just activations or counterpart starts.

    Allow each counterpart its declared ceiling plus four subject phase calls
    per start (work, rectification, return, and recovery headroom). The shared
    ledger remains the authoritative total cap; this is not reserved capacity.
    """
    from worlds.scenarios import actors
    count = sum(a['role']=='counterpart' for a in actors(pack).values())
    return min(720, max(72, count * counterpart_limit + 4 * starts))


def source_hashes():
    selected = [Path(__file__), Path(__file__).with_name("discrepancy_engine.py"),
                Path(__file__).with_name("discrepancy_store.py"),
                Path(__file__).with_name("discrepancy_transport.py"),
                Path(__file__).with_name("discrepancy_repair.py"),
                Path(__file__).with_name("isolated_writer.py"),
                Path(__file__).with_name("builder_worker.py"),
                Path(__file__).with_name("Dockerfile.discrepancy-builder"),
                Path(__file__).with_name("incident_attribution.py"),
                REPO / "worlds/forensics.py", REPO / "worlds/campaign.py",
                REPO / "worlds/driver.py"]
    selected = sorted(set(selected) | set((REPO / "worlds").glob("*.py")) | set((REPO / "evals").glob("*.py")) | set((REPO / "experiments").glob("*.py")))
    return {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in selected}


def append_log(root, event):
    event = {"at": time.time(), **event}
    path = Path(root) / "running-log.jsonl"
    with path.open("a") as stream:
        stream.write(encoded(event) + "\n")
        stream.flush(); os.fsync(stream.fileno())


def prepare(root, image, *, world_names=None, counterpart_limit=6, counterpart_reserve=3,
            budget_directory=None, builder_image="concorde3:discrepancy-builder", interval=INTERVAL,
            maximum_meta_calls=40, review_windows=18, maximum_candidate_repairs=3):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("fresh private campaign root required; no implicit relaunch")
    if interval not in (600, 1200) or type(maximum_meta_calls) is not int or not 40 <= maximum_meta_calls <= 64:
        raise ValueError("bounded cadence and meta-call allowance required")
    if (type(review_windows) is not int or not 2 <= review_windows <= 18
            or type(maximum_candidate_repairs) is not int or not 0 <= maximum_candidate_repairs <= 3):
        raise ValueError("bounded declared review windows and repair lanes required")
    if (type(counterpart_limit) is not int or type(counterpart_reserve) is not int or
            not 1 <= counterpart_limit <= 32 or not 0 <= counterpart_reserve < counterpart_limit):
        raise ValueError("bounded counterpart capacity required")
    names = [arm[0] for arm in ARMS] if world_names is None else list(world_names)
    if not names or len(names) != len(set(names)) or set(names)-{arm[0] for arm in ARMS}:
        raise ValueError("nonempty unique known world subset required")
    selected = [arm for arm in ARMS if arm[0] in names]
    shared = Path(budget_directory).resolve() if budget_directory is not None else None
    if shared is not None and not shared.is_dir():
        raise ValueError("shared budget directory must already exist")
    image_id = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    builder_image_id = command(["docker", "image", "inspect", builder_image, "--format", "{{.Id}}"])
    root.mkdir(parents=True, mode=0o700)
    if shared is not None:
        (root / "world-budget").symlink_to(shared, target_is_directory=True)
    manifest = {"status": "preparing", "prepared_at": time.time(), "started": None,
        "cutoff": None, "duration_seconds": interval * review_windows, "interval_seconds": interval,
        "review_windows": review_windows,
        "source_revision": command(["git", "rev-parse", "HEAD"], cwd=REPO),
        "source_hashes": source_hashes(), "requested_image": image, "image": image_id,
        "builder_image": builder_image_id,
        "worlds": {}, "arms": {}, "model_contract": {
            "subjects": [SUBJECT_MODEL, EFFORT, "ChatGPT subscription"],
            "counterparts": [SUBJECT_MODEL, EFFORT, "ChatGPT subscription"],
            "meta_review_and_curation": [META_MODEL, META_EFFORT, "ChatGPT subscription"],
            "meta_candidate_builder": [META_MODEL, EFFORT, "ChatGPT subscription"]},
        "shared_budget_directory": str(shared or root / "world-budget"),
        "limits": {"subject_count": len(selected), "subject_starts_per_hour": 12,
            "subject_concurrency": 1, "subject_deadline_seconds": 420,
            "counterpart_calls_per_actor_hour": counterpart_limit, "counterpart_response_reserve": counterpart_reserve,
            "shared_calls_per_hour": 720, "shared_concurrency": 24,
            "maximum_meta_calls": maximum_meta_calls, "maximum_candidate_repairs": maximum_candidate_repairs,
            "owner_activation_ceiling_per_hour": 5000},
        "scope": "Fresh simulated worlds only; no real outreach, purchase, API fallback, main merge, or prior experiment resume",
        "limitations": ["Simulated choice/use/payment is not external demand.",
            "Counterpart call scarcity and startup skew are recorded, not treated as rejection.",
            "The engine is an external lab; no review is inserted into a subject activation.",
            "A case may remain unqualified and produce no patch."]}
    for name, pack, seed, actor in selected:
        world = World(root / "worlds" / name)
        world.create(pack, seed, hours=interval * review_windows / 3600)
        select_fresh_subject(world, actor)
        manifest["worlds"][name] = str(world.s.root)
        manifest["arms"][name] = {"pack": pack, "seed": seed, "subject": actor}
    manifest["status"] = "prepared"
    save(root / "cohort.json", manifest)
    DiscrepancyStore(root / "discrepancies.sqlite")
    return manifest


def qualify_timer(root):
    name = "c3-discrepancy-qualify-" + hashlib.sha256(str(root).encode()).hexdigest()[:10]
    command(["sudo", "-n", "systemd-run", "--quiet", "--unit", name,
             "--on-active", "5m", "--timer-property=AccuracySec=1s", "/usr/bin/true"])
    active = command(["sudo", "-n", "systemctl", "is-active", name + ".timer"])
    subprocess.run(["sudo", "-n", "systemctl", "stop", name + ".timer"],
                   capture_output=True, timeout=15)
    return {"unit": name + ".timer", "active_before_cleanup": active == "active",
            "cleaned": True}


def qualify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    if manifest["status"] != "prepared" or source_hashes() != manifest["source_hashes"]:
        raise ValueError("fresh prepared manifest and unchanged controller sources required")
    if command(["git", "rev-parse", "HEAD"], cwd=REPO) != manifest["source_revision"]:
        raise ValueError("controller revision changed after preparation")
    if not (REPO / "bin/concorde3").is_file() or not (REPO / "bin/lab-fixture").is_file():
        raise ValueError("build bin/concorde3 and bin/lab-fixture before qualification")
    codex_binary = shutil.which("codex") or str(Path.home()/'.local/bin/codex')
    check = subprocess.run([codex_binary, "login", "status"], capture_output=True,
                           text=True, timeout=20)
    if check.returncode or "Logged in using ChatGPT" not in check.stdout + check.stderr:
        raise RuntimeError("ChatGPT subscription required; no API fallback")
    store = DiscrepancyStore(root / "discrepancies.sqlite")
    receipts = {}
    label = "luna_subject_transport"
    driver = MetaDriver(root, store, model=SUBJECT_MODEL, effort=EFFORT, timeout=120)
    response, receipt = driver("qualification:" + label,
        "Return actions=[], finish=true, and note exactly " + json.dumps(label + "_OK") + ".")
    if response != {"actions": [], "finish": True, "note": label + "_OK"}:
        raise RuntimeError("exact Luna transport qualification response invalid")
    receipts[label] = receipt
    service_name = "c3-discrepancy-sol-qualify-" + hashlib.sha256(str(root).encode()).hexdigest()[:10]
    output = command(["sudo", "-n", "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--unit", service_name, "--property=User=codex",
        "--property=WorkingDirectory=" + str(REPO), "/usr/bin/python3", "-m",
        "experiments.discrepancy_campaign", "service-qualify", str(root)], timeout=180)
    receipts["sol_meta_transport"] = json.loads(output)
    qualification = {"at": time.time(), "auth": "ChatGPT subscription; no API fallback",
        "models": {"subjects_and_counterparts": [SUBJECT_MODEL, EFFORT],
                   "meta_review_and_curation": [META_MODEL, META_EFFORT],
                   "meta_candidate_builder": [META_MODEL, EFFORT]},
        "transport_receipts": receipts,
        "timer": qualify_timer(root), "docker_inventory_before_launch": command(
            ["docker", "ps", "--format", "{{.Names}}"]),
        "binary_sha256": hashlib.sha256((REPO / "bin/concorde3").read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256((REPO / "bin/lab-fixture").read_bytes()).hexdigest(),
        "codex_binary": codex_binary,
        "image": command(["docker", "image", "inspect", manifest["image"], "--format", "{{.Id}}"])}
    save(root / "qualification.json", qualification)
    manifest.update(status="qualified", qualification=str(root / "qualification.json"))
    save(root / "cohort.json", manifest)
    return qualification


def service_qualify(root):
    """Exact no-tool Sol call under the same system service environment as run."""
    root = Path(root).resolve()
    store = DiscrepancyStore(root / "discrepancies.sqlite")
    label = "sol_meta_transport"
    response, receipt = MetaDriver(root, store, model=META_MODEL, effort=META_EFFORT,
        timeout=120)("qualification:" + label,
        "Return actions=[], finish=true, and note exactly " + json.dumps(label + "_OK") + ".")
    if response != {"actions": [], "finish": True, "note": label + "_OK"}:
        raise RuntimeError("exact Sol service transport qualification response invalid")
    return receipt


def arm(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    if manifest["status"] != "qualified" or source_hashes() != manifest["source_hashes"]:
        raise ValueError("qualified unchanged controller required")
    started = time.time()
    cutoff = started + manifest.get("duration_seconds", DURATION)
    manifest.update(status="arming", started=started, cutoff=cutoff,
                    controller_pid=os.getpid(), startup={})
    save(root / "cohort.json", manifest)
    # Install independent cutoff protection before any world becomes live.
    try:
        manifest["timers"] = install_timers(root, manifest)
        save(root / "cohort.json", manifest)
        for index, (name, location) in enumerate(manifest["worlds"].items()):
            world = World(Path(location))
            actor = manifest["arms"][name]["subject"]
            with world.s.transaction() as db:
                cfg = world.s.meta(db, "config")
                cfg.update(started=started, cutoff=cutoff, period_seconds=1800,
                    tick_seconds=600, model=SUBJECT_MODEL, effort=EFFORT,
                    baseline_starts=12, maximum_starts=12, activation_deadline=420,
                    calls_per_hour=local_call_allowance(manifest['arms'][name]['pack'],
                        manifest['limits']['counterpart_calls_per_actor_hour']), concurrency=3, shared_calls_per_hour=720,
                    shared_concurrency=24, counterpart_calls_per_hour=manifest["limits"]["counterpart_calls_per_actor_hour"],
                    counterpart_response_reserve=manifest["limits"]["counterpart_response_reserve"], staff_per_period=24,
                    experiment="discrepancy-engine-v1")
                world.s.meta(db, "config", cfg)
                for offset, project in enumerate(world.s.rows(db, "project")):
                    world.s.revise(db, project, staff_remaining=24)
                    continuation = world.s.get(db, "continuation:" + project["owner"])
                    world.s.revise(db, continuation, next_at=started + 30 + index * 5 + offset * 8,
                        reason="Fresh mixed operating life; startup stagger only")
                world.s.event(db, "_operator", "discrepancy_campaign_armed", {
                    "started": started, "cutoff": cutoff, "subject": actor,
                    "model": SUBJECT_MODEL, "effort": EFFORT,
                    "reason": "Owner-authorized fresh finite simulated discrepancy campaign"})
        DiscrepancyStore(root / "discrepancies.sqlite").schedule_rounds(started, cutoff,
            manifest.get("interval_seconds", INTERVAL), windows=manifest.get("review_windows",18))
        manifest["status"] = "running"
        save(root / "cohort.json", manifest)
        return manifest
    except BaseException as error:
        try: freeze_scope(root, reason="arming/timer failure")
        except Exception: pass
        manifest.update(status="closed_requires_review", arming_error=str(error))
        save(root / "cohort.json", manifest)
        raise


def install_timers(root, manifest):
    token = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    emergency = "c3-discrepancy-" + token + "-freeze"
    final = "c3-discrepancy-" + token + "-controller-stop"
    unit = os.environ.get("DISCREPANCY_UNIT", "c3-discrepancy-" + token)
    when = dt.datetime.fromtimestamp(manifest["cutoff"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    end = dt.datetime.fromtimestamp(manifest["cutoff"] + 600, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    command(["sudo", "-n", "systemd-run", "--quiet", "--unit", emergency,
        "--on-calendar", when, "--timer-property=AccuracySec=1s", "--property=User=codex",
        "--property=WorkingDirectory=" + str(REPO), "/usr/bin/python3", "-m",
        "experiments.discrepancy_campaign", "emergency-freeze", str(root)])
    command(["sudo", "-n", "systemd-run", "--quiet", "--unit", final,
        "--on-calendar", end, "--timer-property=AccuracySec=1s", "systemctl", "stop", unit + ".service"])
    return {"emergency_freeze": emergency + ".timer", "controller_stop": final + ".timer",
            "controller_service": unit + ".service", "freeze_at": manifest["cutoff"],
            "closure_deadline": manifest["cutoff"] + 600}


def seed_capture(root, name, actor, instance, image):
    root, instance = Path(root), Path(instance)
    manifest = json.loads((root / "cohort.json").read_text())
    state = json.loads((instance / ".concorde2/state.json").read_text())
    cfg = state["config"]
    if actor != manifest["arms"][name]["subject"] or image != manifest["image"]:
        raise ValueError("launch provenance mismatch")
    if state.get("activations") or (cfg.get("model"), cfg.get("effort")) != (SUBJECT_MODEL, EFFORT):
        raise ValueError("fresh exact Luna xhigh subject required")
    if cfg.get("starts_per_hour") != 12 or cfg.get("deadline_seconds") != 420:
        raise ValueError("subject resource contract mismatch")
    freeze_at = dt.datetime.fromisoformat(cfg["freeze_at"].replace("Z", "+00:00")).timestamp()
    if abs(freeze_at - manifest["cutoff"]) > .01:
        raise ValueError("subject cutoff mismatch")
    target = root / "seeds" / name
    target.mkdir(parents=True, mode=0o700)
    refs = {}
    for relative in (".concorde2/state.json", ".concorde2/events.jsonl", "brain.json"):
        data = (instance / relative).read_bytes()
        path = target / Path(relative).name
        with path.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        path.chmod(0o600)
        refs[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    save(target / "manifest.json", {"captured": time.time(), "subject": actor,
        "image": image, "model": cfg["model"], "effort": cfg["effort"],
        "startup_loss_seconds": time.time() - manifest["started"], "files": refs})


def world_worker(root, name):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    if manifest["status"] != "running" or time.time() >= manifest["cutoff"]:
        raise ValueError("live unexpired campaign required; no restart")
    os.environ["WORLD_BUDGET_FILE"] = str(root / "world-budget" / "calls.jsonl")
    arm = manifest["arms"][name]
    campaign.run(World(Path(manifest["worlds"][name])), image=manifest["image"],
        subjects=[arm["subject"]], before_subject_start=lambda actor, instance, image:
            seed_capture(root, name, actor, instance, image))


def review_round(root, number, *, terminal=False):
    root = Path(root).resolve()
    store = DiscrepancyStore(root / "discrepancies.sqlite")
    manifest = json.loads((root / "cohort.json").read_text())
    started = store.start_round(number)
    capture_ref = None
    review_id = None
    try:
        snapshot = forensics.snapshot(root, root / "audit", number,
                                      scheduled=manifest["started"] + number * manifest.get("interval_seconds", INTERVAL))
        capture_ref = str(root / "audit/rounds" / f"{number:02d}.json")
        forensics.digest_round(root / "audit", number)
        packet = build_packet(root / "audit", number, manifest, store)
        packet_path = root / "reviews" / f"{number:02d}" / "packet.json"
        save(packet_path, packet)
        review_id = store.begin_review(number, "terminal" if terminal else "interval")
        hard_until = manifest["cutoff"] + 480 if terminal else min(
            manifest["cutoff"], manifest["started"] + (number + 1) * manifest.get("interval_seconds", INTERVAL) - 30)
        # A realistic balanced cohort packet can require more than the generic
        # short-call default. This remains bounded inside the fixed cadence.
        driver = MetaDriver(root, store, effort=META_EFFORT, timeout=420,
                            hard_until=hard_until, response_schema=review_response_schema(packet))
        prompt = REVIEW_PROMPT + "\n" + encoded(packet)
        response, receipt = driver(f"review:{number}", prompt)
        value = validate_review(response, packet)
        response_path = root / "reviews" / f"{number:02d}" / "review.json"
        save(response_path, {"review": value, "receipt": receipt})
        for judgment in value["judgments"]:
            if not judgment["case_id"]:
                world, _, actor = judgment["subject"].partition("/")
                exposure = packet["denominators"].get(judgment["subject"], {
                    "censored": True, "reason": "subject key did not match a denominator"})
                judgment["case_id"] = store.nominate(round_number=number,
                    world=world or "cohort", subject=actor or judgment["subject"],
                    category="intelligent_trajectory:" + judgment["category"],
                    polarity="positive" if judgment["disposition"] == "healthy_control" else "neutral" if judgment["disposition"] in ("censored", "quarantine") else "suspected", severity=judgment["severity"],
                    evidence_ref=judgment["evidence"][0], observed=judgment["observed"],
                    exposure=exposure,
                    episode_key="sol:" + judgment["subject"] + ":" + judgment["episode_key"],
                    censored=judgment["disposition"] == "censored")
                packet["nominated_case_ids"].append(judgment["case_id"])
            store.add_judgment(review_id, judgment)
        store.finish_review(review_id, status="completed_partial" if value["rejected_items"] else "completed",
            packet_sha256=hashlib.sha256(packet_path.read_bytes()).hexdigest(),
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            response_ref=str(response_path), usage=receipt["usage"])
        store.finish_round(number, "terminal" if terminal else "reviewed",
                           capture_ref=capture_ref, review_ref=str(response_path))
        append_log(root, {"kind": "intelligent_review", "round": number,
            "scheduled": manifest["started"] + number * manifest.get("interval_seconds", INTERVAL), "started": started,
            "finished": time.time(), "terminal": terminal,
            "judgments": len(value["judgments"]), "cases": packet["nominated_case_ids"],
            "validation_status": value["validation_status"], "rejected_items": value["rejected_items"]})
    except BaseException as error:
        if review_id is not None:
            rows = store.rows("reviews", "id=?", (review_id,))
            if rows and rows[0]["status"] == "running":
                try:
                    store.finish_review(review_id, status="failed", error=str(error)[:1000])
                except Exception:
                    pass
        try:
            store.finish_round(number, "failed", capture_ref=capture_ref, error=str(error)[:1000])
        except Exception:
            pass
        append_log(root, {"kind": "review_failure", "round": number,
                          "error": str(error)[:1000]})
        raise
    if not terminal:
        try:
            maybe_curate(root, number, packet, value, hard_until)
        except Exception as error:
            # Optional casework cannot rewrite a completed cadence review or
            # manufacture a monitoring failure.
            append_log(root, {"kind": "curation_failure", "round": number,
                              "error": str(error)[:1000]})
    return value


def curation_attempt(root, store, choice, packet, number):
    """A later evidenced observation may answer observe_more, never reroll a probe."""
    jobs = store.rows("jobs", "case_id=? AND kind='curation'", (choice["case_id"],))
    if not jobs:
        return 1
    if len(jobs) != 1 or jobs[0]["status"] != "rejected" or store.rows(
            "jobs", "case_id=? AND kind='baseline_probe'", (choice["case_id"],)):
        return None
    original = json.loads(jobs[0]["specification"])
    if number <= original["round"]:
        return None
    path = Path(jobs[0]["result_ref"] or "").resolve()
    if not path.is_relative_to((Path(root)/"cases"/choice["case_id"]).resolve()) or not path.is_file():
        return None
    previous = json.loads(path.read_text())["curation"]
    if previous["decision"] != "observe_more":
        return None
    old = set(original["review_judgment"].get("activation_ids", []))
    requested = set(choice.get("activation_ids", []))
    known = supplied_activation_ids(packet, choice["subject"])
    return 2 if requested <= known and requested-old else None


def maybe_curate(root, number, packet, review, hard_until):
    store = DiscrepancyStore(Path(root) / "discrepancies.sqlite")
    # Preserve one Sol call for every unattempted cadence boundary. Optional
    # work cannot consume monitoring capacity, and at most three candidate lanes
    # can ever be admitted in this campaign.
    manifest_path = Path(root)/"cohort.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    mandatory_remaining = manifest.get("review_windows",18) - number
    if len(store.rows("calls")) + mandatory_remaining + 11 > manifest.get("limits", {}).get("maximum_meta_calls", 40):
        return None
    if len(store.rows("jobs", "kind='baseline_probe'")) >= manifest.get("limits",{}).get("maximum_candidate_repairs",3):
        return None
    choices = [j for j in review["judgments"] if j["disposition"] == "curate"
               and j.get("severity") != "info"
               and j["case_id"]
               and case_scope_matches(store.rows("signals", "case_id=?", (j["case_id"],)), j["subject"])
               # Model/context interaction is a legitimate question for curation,
               # not proof that C3 must change. The curator may reject the analogy.
               and j["likely_layer"] in {"C3", "model", "uncertain"}
               and j["confidence"] in {"medium", "high"}
               and curation_attempt(root, store, j, packet, number)]
    if not choices or time.time() >= hard_until - 210:
        return None
    choice = choices[0]
    case_id = choice["case_id"]
    attempt = curation_attempt(root, store, choice, packet, number)
    store.set_case(case_id, "curating", selected=True)
    job = store.create_job(case_id, "curation", {"round": number, "attempt": attempt,
        "review_judgment": choice}, status="running")
    review_id = store.begin_review(number, "curation")
    try:
        curated_packet = targeted_case_packet(Path(root) / "audit", number, packet,
                                              choice["subject"], evidence=choice["evidence"],
                                              activation_ids=choice.get("activation_ids", []))
        driver = MetaDriver(root, store, effort=META_EFFORT, hard_until=hard_until,
            response_schema=curation_response_schema(curated_packet, case_id))
        prompt = CURATOR_PROMPT + "\n" + encoded({"selected": choice,
            "case_id": case_id, "evidence_packet": curated_packet,
            "first_version_scope": "May map to a pre-existing qualified probe only as analogous mechanism evidence. Live snapshot is not exact replay; incident-derived fixture authoring is not yet automatic."})
        response, receipt = driver("curation:" + case_id, prompt)
        value = validate_curation(response, curated_packet, case_id)
        path = Path(root) / "cases" / case_id / "curation.json"
        if attempt > 1:
            path = path.parent / "curation-attempts" / f"{attempt:02d}" / "curation.json"
        save(path, {"curation": value, "receipt": receipt,
            "limitation": "Existing-family mapping is analogous mechanism evidence, not automatic incident-derived replay."})
        store.finish_review(review_id, status="completed",
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            response_ref=str(path), usage=receipt["usage"])
        if value["decision"] == "qualified_existing_probe":
            criteria = value["frozen_criteria"]
            criteria_path = Path(root) / "cases" / case_id / "frozen-criteria.json"
            save(criteria_path, {"case_id": case_id, "created_before_probe": time.time(),
                "criteria": criteria, "probe": value["probe_family"],
                "cells": value["baseline_cells"], "base_revision": json.loads(
                    (Path(root) / "cohort.json").read_text())["source_revision"]})
            criteria_path.chmod(0o400)
            digest = hashlib.sha256(criteria_path.read_bytes()).hexdigest()
            store.update_job(job, "passed", result_ref=str(path), criteria_sha256=digest)
            store.set_case(case_id, "qualified")
            # The runnable baseline/candidate lane is explicit; launch remains a
            # separate bounded controller decision so it cannot overlap cadence.
            spec = {"curation": str(path), "criteria": str(criteria_path),
                    "criteria_sha256": digest, "dispatch": "python3 -m experiments.discrepancy_repair baseline ROOT CASE_ID"}
            queued = store.create_job(case_id, "baseline_probe", spec)
            append_log(root, {"kind": "probe_queued", "case": case_id, "job": queued,
                              "reason": "qualified analogous C3-specific mechanism; unchanged baseline must run first"})
        else:
            store.update_job(job, "rejected", result_ref=str(path))
            store.set_case(case_id, "quarantined" if value["decision"] == "quarantine" else "suspected")
        return value
    except BaseException as error:
        # A later queue/storage failure does not invalidate a completed model
        # review or its frozen criteria. Preserve terminal receipts, and retain
        # the original orchestration fault instead of raising a second transition.
        if store.rows("reviews", "id=?", (review_id,))[0]["status"] == "running":
            store.finish_review(review_id, status="failed", error=str(error)[:1000])
        if store.rows("jobs", "id=?", (job,))[0]["status"] in {"queued", "running"}:
            store.update_job(job, "failed", result_ref=str(error)[:1000])
        store.set_case(case_id, "quarantined")
        raise


def freeze_scope(root, children=None, reason="fixed campaign cutoff"):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    errors = []
    if children:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
    # The independent timer can overlap the controller's completed freeze and
    # new terminal review. Preserve only that bounded read-only postmortem, never
    # subjects, candidate jobs, ordinary reviews or a generic meta-process class.
    cutoff = manifest.get("cutoff")
    preserve_terminal = isinstance(cutoff, (int, float)) and cutoff <= time.time() < cutoff + 480
    stop_receipt = stop_active(root, preserve_purposes=(f"review:{manifest.get('review_windows',18)}",) if preserve_terminal else ())
    lab_stops = []
    try:
        names = command(["docker", "ps", "--filter", "label=concorde.lab=true",
                         "--format", "{{.Names}}"]).splitlines()
        for name in names:
            inspection = json.loads(command(["docker", "inspect", name]))[0]
            sources = [Path(m["Source"]).resolve() for m in inspection.get("Mounts", [])
                       if m.get("Type") == "bind"]
            if any(source.is_relative_to(root) for source in sources):
                subprocess.run(["docker", "stop", "-t", "5", name], capture_output=True, timeout=20)
                running = command(["docker", "inspect", name, "--format", "{{.State.Running}}"])
                lab_stops.append({"container": name, "verified_stopped": running == "false",
                                  "matched_mounts": [str(s) for s in sources if s.is_relative_to(root)]})
    except Exception as error:
        errors.append({"lab_container_freeze": str(error)})
    for final_pass in (False, True):
        for name, location in manifest["worlds"].items():
            os.environ["WORLD_BUDGET_FILE"] = str(root / "world-budget" / "calls.jsonl")
            try:
                campaign.freeze(World(Path(location)))
            except Exception as error:
                errors.append({"world": name, "pass": final_pass, "error": str(error)})
        if children and not final_pass:
            for child in children.values():
                try:
                    child.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=10)
    DiscrepancyStore(root / "discrepancies.sqlite").censor_open(reason, preserve_terminal=preserve_terminal,
        terminal_round=manifest.get("review_windows",18))
    receipt = {"at": time.time(), "reason": reason, "world_errors": errors,
               "meta_and_jobs": stop_receipt, "lab_containers": lab_stops,
               "children": {k: v.poll() for k, v in (children or {}).items()}}
    target = root / "freeze-receipts" / (str(time.time_ns()) + ".json")
    save(target, receipt)
    receipt["receipt_ref"] = str(target)
    return receipt


def start_queued_repair(root, running):
    """Start at most one qualified baseline-first lane, never near cutoff."""
    root = Path(root).resolve()
    if running and running.poll() is None:
        return running
    manifest = json.loads((root / "cohort.json").read_text())
    if manifest.get("limits",{}).get("maximum_candidate_repairs",3) == 0:
        return None
    if time.time() >= manifest["cutoff"] - 6000:
        return None
    store = DiscrepancyStore(root / "discrepancies.sqlite")
    queued = store.rows("jobs", "kind='baseline_probe' AND status='queued' ORDER BY created LIMIT 1")
    if not queued:
        return None
    case_id = queued[0]["case_id"]
    log = root / "cases" / case_id / "repair-controller.log"
    stream = log.open("x")
    process = subprocess.Popen([sys.executable, "-m", "experiments.discrepancy_repair",
        "baseline", str(root), case_id], cwd=REPO, stdout=stream,
        stderr=subprocess.STDOUT, start_new_session=True)
    from .discrepancy_transport import process_start_ticks
    save(root / "repair-active" / (case_id + ".json"), {"case": case_id,
        "pid": process.pid, "pgid": process.pid,
        "process_start_ticks": process_start_ticks(process.pid), "started": time.time(),
        "log": str(log)})
    process._discrepancy_stream = stream
    process._discrepancy_case = case_id
    append_log(root, {"kind": "repair_lane_started", "case": case_id,
                      "pid": process.pid, "baseline_first": True})
    return process


def reconcile_repair(root, process):
    if process is None or process.poll() is None:
        return process
    process._discrepancy_stream.close()
    (Path(root) / "repair-active" / (process._discrepancy_case + ".json")).unlink(missing_ok=True)
    append_log(root, {"kind": "repair_lane_finished", "case": process._discrepancy_case,
                      "exit_code": process.returncode})
    if process.returncode == 0 and getattr(process, '_discrepancy_kind', 'repair') == 'repair':
        from .discrepancy_canary import start
        try:
            return start(root, process._discrepancy_case)
        except Exception as error:
            append_log(root, {'kind':'canary_not_started','case':process._discrepancy_case,
                              'reason':str(error)[:1000]})
    return None


def seed_inventory(root, manifest):
    """Verify pre-dispatch seed receipts; directory existence is not readiness."""
    receipts, missing = {}, []
    for name in manifest["worlds"]:
        target = Path(root) / "seeds" / name
        path = target / "manifest.json"
        if not path.exists():
            missing.append(name)
            continue
        raw = path.read_bytes()
        receipt = json.loads(raw)
        if receipt["subject"] != manifest["arms"][name]["subject"] or receipt["image"] != manifest["image"]:
            raise ValueError("seed capture provenance mismatch: " + name)
        for relative in (".concorde2/state.json", ".concorde2/events.jsonl", "brain.json"):
            data = (target / Path(relative).name).read_bytes()
            expected = receipt["files"][relative]
            if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("seed capture integrity mismatch: " + name)
        receipts[name] = {"manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "captured": receipt["captured"], "startup_loss_seconds": receipt["startup_loss_seconds"]}
    return {"receipts": receipts, "missing": missing}


def await_seeds(root, manifest, children, stopping, maximum_seconds=120):
    # Startup consumes the original window; it never moves the fixed cutoff.
    deadline = min(time.time() + maximum_seconds, manifest["cutoff"])
    while True:
        inventory = seed_inventory(root, manifest)
        failed = {name: child.poll() for name, child in children.items() if child.poll() is not None}
        if stopping() or failed:
            raise RuntimeError("startup stopped or worker exited: " + str(failed))
        if not inventory["missing"]:
            append_log(root, {"kind": "seeds_verified", **inventory,
                "meaning": "individual pre-dispatch seeds; not a simultaneous world snapshot"})
            return inventory
        if time.time() >= deadline:
            raise RuntimeError("seed capture startup deadline: " + str(inventory["missing"]))
        time.sleep(min(.25, max(0, deadline-time.time())))


def closure_issues(root, store, closure, freeze_result):
    """Finishing the Python controller is not evidence of clean experimental closure."""
    root = Path(root); issues = {}; manifest = {}
    if not isinstance(closure, dict) or not isinstance(closure.get("errors"), list):
        issues["closure_inspection"] = "missing or invalid closure inspection"
    elif closure["errors"]:
        issues["world_closure"] = closure["errors"]
    # Empty error summaries are not positive evidence that the declared worlds
    # were inspected. Cross-check the captured detail, without rewriting it or
    # claiming this is a fresh process inspection.
    try:
        manifest = json.loads((root/"cohort.json").read_text())
        if (not isinstance(manifest, dict) or not isinstance(manifest.get("worlds"), dict)
                or not manifest["worlds"] or not isinstance(manifest.get("arms"), dict)
                or set(manifest["arms"]) != set(manifest["worlds"])
                or type(manifest.get("cutoff")) not in (int, float)):
            raise ValueError("complete declared world/subject/cutoff scope required")
        expected = {name: arm["subject"] for name, arm in manifest["arms"].items()}
        if not all(isinstance(actor, str) and actor for actor in expected.values()):
            raise ValueError("declared subject identity required")
    except (OSError, ValueError, TypeError, KeyError) as error:
        issues["closure_manifest"] = str(error)
    else:
        worlds = closure.get("worlds") if isinstance(closure, dict) else None
        if not isinstance(worlds, dict) or set(worlds) != set(expected):
            issues["closure_world_scope"] = {"expected": sorted(expected),
                "observed": sorted(worlds) if isinstance(worlds, dict) else None}
        for name, actor in expected.items():
            world = worlds.get(name) if isinstance(worlds, dict) else None
            valid = isinstance(world, dict)
            if valid:
                deployed, inactive, modes = (world.get(k) for k in ("deployed_subjects", "inactive_subjects", "modes"))
                valid = (isinstance(deployed, list) and isinstance(inactive, list)
                    and all(isinstance(a, str) for a in deployed+inactive)
                    and isinstance(modes, dict) and actor in deployed
                    and set(modes) == set(deployed+inactive) and all(v == "frozen" for v in modes.values())
                    and world.get("frozen") is True and type(world.get("pending_calls")) is int
                    and world["pending_calls"] == 0 and world.get("running_containers") == []
                    and world.get("configured_cutoff") == manifest["cutoff"]
                    and isinstance(world.get("contracts"), list)
                    and all(isinstance(c, dict) and c.get("review_reasons") == [] for c in world["contracts"]))
            if not valid:
                issues.setdefault("closure_world_details", []).append(name)
    rounds = {r["number"]: r["status"] for r in store.rows("rounds")}
    windows = manifest.get("review_windows",18)
    incomplete = [n for n in range(1, windows+1) if rounds.get(n) != ("terminal" if n == windows else "reviewed")]
    reviews = store.rows("reviews", "kind IN ('interval','terminal')")
    complete = {r["round"] for r in reviews if r["status"] == "completed"}
    incomplete = sorted(set(incomplete) | (set(range(1, windows+1))-complete))
    if incomplete: issues["incomplete_review_rounds"] = incomplete
    bad = [{"round": r["round"], "status": r["status"]} for r in reviews if r["status"] != "completed"]
    if bad: issues["noncomplete_reviews"] = bad
    active = [str(p) for folder in ("meta-active", "jobs-active", "repair-active", "container-active") for p in (root/folder).glob("*.json")]
    if active: issues["retained_process_inventory"] = active
    open_calls = store.rows("calls", "status IN ('reserved','running')")
    if open_calls: issues["open_calls"] = [r["id"] for r in open_calls]
    open_jobs = store.rows("jobs", "status IN ('queued','running')")
    if open_jobs: issues["open_jobs"] = [r["id"] for r in open_jobs]
    receipts = [freeze_result]
    for path in sorted((root/"freeze-receipts").glob("*.json")):
        try: receipts.append(json.loads(path.read_text()))
        except (OSError, ValueError) as error:
            issues.setdefault("unreadable_freeze_receipts", []).append({"path": str(path), "error": str(error)})
    freeze_errors = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            freeze_errors.append("missing freeze receipt"); continue
        if (type(receipt.get("at")) not in (int, float) or not receipt.get("reason")
                or not isinstance(receipt.get("world_errors"), list)
                or not isinstance(receipt.get("meta_and_jobs"), dict)
                or not isinstance(receipt["meta_and_jobs"].get("errors"), list)
                or not isinstance(receipt.get("lab_containers"), list)
                or not all(isinstance(r, dict) for r in receipt["lab_containers"])
                or not isinstance(receipt.get("children"), dict)):
            freeze_errors.append("incomplete freeze receipt"); continue
        if receipt.get("world_errors"): freeze_errors.append({"world_errors": receipt["world_errors"]})
        if receipt.get("meta_and_jobs", {}).get("errors"): freeze_errors.append({"process_errors": receipt["meta_and_jobs"]["errors"]})
        not_stopped = [r for r in receipt.get("lab_containers", []) if not r.get("verified_stopped")]
        if not_stopped: freeze_errors.append({"unverified_containers": not_stopped})
        children = [name for name, code in receipt.get("children", {}).items() if code is None]
        if children: freeze_errors.append({"live_children": children})
        failed_children = {name: code for name, code in receipt["children"].items() if code is not None and code != 0}
        if failed_children: freeze_errors.append({"failed_children": failed_children})
    if freeze_errors: issues["freeze_errors"] = freeze_errors
    return issues


def run(root):
    root = Path(root).resolve()
    manifest = arm(root)
    windows = manifest.get("review_windows",18)
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    children, streams = {}, []
    repair = None
    errors = []
    try:
        for name in manifest["worlds"]:
            log = (root / "controller-logs" / (name + ".log"))
            log.parent.mkdir(parents=True, exist_ok=True)
            stream = log.open("x"); streams.append(stream)
            child = subprocess.Popen([sys.executable, "-m", "experiments.discrepancy_campaign",
                "world-worker", str(root), "--name", name], cwd=REPO,
                stdout=stream, stderr=subprocess.STDOUT)
            children[name] = child
            manifest["startup"][name] = {"pid": child.pid, "spawned": time.time(),
                "startup_loss_seconds": time.time() - manifest["started"]}
        manifest["processes"] = {name: child.pid for name, child in children.items()}
        save(root / "cohort.json", manifest)
        await_seeds(root, manifest, children, lambda: stopping)
        store = DiscrepancyStore(root / "discrepancies.sqlite")
        store.start_round(0)
        first = forensics.snapshot(root, root / "audit", 0, scheduled=manifest["started"])
        forensics.digest_round(root / "audit", 0)
        store.finish_round(0, "captured", capture_ref=str(root / "audit/rounds/00.json"))
        append_log(root, {"kind": "initial_capture", "round": 0, "errors": first["errors"],
                          "meaning": "initial capture only; intelligent reviews follow the declared schedule"})
        if first["errors"]:
            errors.append({"initial_capture": first["errors"]})
        for number in range(1, windows):
            scheduled = manifest["started"] + number * manifest.get("interval_seconds", INTERVAL)
            while not stopping and time.time() < scheduled:
                failed = {name: child.poll() for name, child in children.items()
                          if child.poll() is not None}
                if failed:
                    raise RuntimeError("world controller exited early: " + str(failed))
                repair = reconcile_repair(root, repair)
                time.sleep(min(5, scheduled - time.time()))
            if stopping or time.time() >= manifest["cutoff"]:
                break
            if time.time() >= scheduled + manifest.get("interval_seconds", INTERVAL):
                store.start_round(number)
                store.finish_round(number, "gap", error="cadence boundary missed; no retrospective review fabricated")
                append_log(root, {"kind": "monitoring_gap", "round": number,
                                  "scheduled": scheduled, "actual": time.time()})
                continue
            try:
                review_round(root, number)
                repair = reconcile_repair(root, repair)
                repair = start_queued_repair(root, repair)
            except Exception as error:
                errors.append({"round": number, "error": str(error)})
        while not stopping and time.time() < manifest["cutoff"]:
            time.sleep(min(2, manifest["cutoff"] - time.time()))
    except BaseException as error:
        errors.append({"controller": str(error)})
    finally:
        all_children = dict(children)
        if repair is not None and repair.poll() is None:
            all_children["repair"] = repair
        freeze_result = freeze_scope(root, all_children, "fixed campaign cutoff" if time.time() >= manifest["cutoff"] else "controller stop/fault")
        manifest = json.loads((root / "cohort.json").read_text())
        manifest.update(status="frozen", frozen_at=time.time(), errors=errors,
                        exit_codes={name: child.poll() for name, child in children.items()})
        save(root / "cohort.json", manifest)
        try:
            review_round(root, windows, terminal=True)
        except Exception as error:
            errors.append({"terminal_review": str(error)})
        closure = None
        try:
            closure = forensics.closure(root, root / "audit")
            save(root / "closure.json", closure)
        except Exception as error:
            errors.append({"closure": str(error)})
        store = DiscrepancyStore(root / "discrepancies.sqlite")
        labs = []
        for case in (root / "cases").glob("case-*"):
            for relative in ("baseline/primary", "baseline/holdout",
                             "candidate/primary", "candidate/holdout"):
                path = case / relative
                if list(path.glob("*/manifest.json")):
                    labs.append(path)
        try:
            accounting = campaign_account.collect(labs=labs,
                worlds=[Path(path) for path in manifest["worlds"].values()])
        except Exception as error:
            # Accounting is evidence aggregation after every in-scope process has
            # already been frozen. A partial receipt must not prevent closure.
            errors.append({"accounting": str(error)})
            accounting = {"status": "inconclusive", "error": str(error),
                "lab_roots_considered": [str(path) for path in labs],
                "world_roots_considered": list(manifest["worlds"].values())}
        partial_reviews = store.rows("reviews", "status='completed_partial'")
        completion_gaps = closure_issues(root, store, closure, freeze_result)
        if completion_gaps: errors.append({"closure_checks": completion_gaps})
        from .discrepancy_canary import inventory as canary_inventory
        try:
            canary = canary_inventory(root)
        except Exception as error:
            canary = {'status':'inspection_gap','error':str(error)[:1000]}
            errors.append({'canary_inventory':str(error)[:1000]})
        final = {"status": "closed_requires_review" if errors or partial_reviews else "closed",
            "started": manifest["started"], "cutoff": manifest["cutoff"],
            "finished": time.time(), "rounds": store.rows("rounds"),
            "cases": store.rows("cases"), "jobs": store.rows("jobs"), "partial_reviews": partial_reviews,
            'canary': canary,
            "usage": {"meta": usage_totals(store.rows("calls")),
                "qualification_included_in_meta": True,
                "live_and_sim": accounting,
                "scope_note": "live_concordes, world_counterparts, sim_tests and Sol meta remain separate; subscription has no invented API bill"},
            "errors": errors, "closure_issues": completion_gaps, "freeze_receipts": [str(path) for path in sorted(
                (root / "freeze-receipts").glob("*.json"))]}
        save(root / "final-report.json", final)
        manifest.update(status=final["status"], finished=final["finished"], errors=errors,
                        final_report=str(root / "final-report.json"))
        save(root / "cohort.json", manifest)
        for stream in streams: stream.close()


def emergency_freeze(root):
    root = Path(root).resolve()
    receipt = freeze_scope(root, reason="independent fixed-cutoff timer")
    append_log(root, {"kind": "emergency_freeze", "receipt": receipt})
    return receipt


def start_health_service(root):
    """Separate read-only sampler; its guard derives from the actual live cutoff."""
    root = Path(root).resolve()
    manifest = json.loads((root/"cohort.json").read_text())
    receipt_path = root/"health-service.json"
    if receipt_path.exists():
        raise ValueError("health sampler already attempted; no implicit relaunch")
    cutoff = manifest.get("cutoff")
    if (manifest.get("status") != "running" or not isinstance(cutoff, (int, float))
            or not math.isfinite(cutoff) or time.time() >= cutoff+610):
        raise ValueError("live campaign with remaining observation window required")
    until = cutoff+610
    unit = "c3-discrepancy-health-"+hashlib.sha256(str(root).encode()).hexdigest()[:10]
    args = ["sudo", "-n", "systemd-run", "--quiet", "--unit", unit,
        "--property=User=codex", "--property=WorkingDirectory="+str(REPO),
        "--property=RuntimeMaxSec="+str(math.ceil(until-time.time())+60),
        "/usr/bin/python3", "-m", "experiments.discrepancy_health", str(root),
        "--output", str(root/"health"), "--until", str(until)]
    receipt = {"unit": unit+".service", "until": until, "command": args,
        "status": "starting", "meaning": "Read-only thirty-second health samples; intelligent reviews remain on their own twenty-minute cadence"}
    save(receipt_path, receipt)
    try:
        command(args)
        if command(["sudo", "-n", "systemctl", "is-active", unit+".service"]) != "active":
            raise RuntimeError("health sampler did not become active")
        receipt["status"] = "active"
    except BaseException as error:
        receipt.update(status="failed", error=str(error)[:1000])
        save(receipt_path, receipt)
        raise
    save(receipt_path, receipt)
    return receipt


def start_service(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "cohort.json").read_text())
    if manifest["status"] != "qualified":
        raise ValueError("qualified campaign required")
    token = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    unit = "c3-discrepancy-" + token
    command(["sudo", "-n", "systemd-run", "--quiet", "--unit", unit,
        "--property=User=codex", "--property=WorkingDirectory=" + str(REPO),
        "--property=Environment=DISCREPANCY_UNIT=" + unit,
        "--property=StandardOutput=append:" + str(root / "controller.log"),
        "--property=StandardError=append:" + str(root / "controller.log"),
        "/usr/bin/python3", "-m", "experiments.discrepancy_campaign", "run", str(root)])
    deadline = time.time() + 60
    while time.time() < deadline:
        current = json.loads((root / "cohort.json").read_text())
        if current["status"] == "running" and current.get("processes"):
            health = start_health_service(root)
            return {"unit": unit + ".service", "main_pid": command(["sudo", "-n", "systemctl",
                "show", unit + ".service", "--property=MainPID", "--value"]),
                "started": current["started"], "cutoff": current["cutoff"],
                "next_review": current["started"] + current.get("interval_seconds", INTERVAL), "root": str(root), "health": health}
        state = subprocess.run(["sudo", "-n", "systemctl", "is-failed", unit + ".service"],
                               capture_output=True, text=True, timeout=15)
        if state.stdout.strip() == "failed":
            raise RuntimeError("durable controller service failed during launch")
        time.sleep(1)
    raise RuntimeError("controller did not reach running state")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "qualify", "service-qualify", "start", "run", "world-worker", "emergency-freeze"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--image", default="concorde3:discrepancy")
    parser.add_argument("--builder-image", default="concorde3:discrepancy-builder")
    parser.add_argument("--name")
    parser.add_argument("--world", action="append", dest="world_names")
    parser.add_argument("--counterpart-limit", type=int, default=6)
    parser.add_argument("--counterpart-reserve", type=int, default=3)
    parser.add_argument("--budget-directory", type=Path)
    parser.add_argument("--interval", type=int, choices=(600, 1200), default=INTERVAL)
    parser.add_argument("--maximum-meta-calls", type=int, default=40)
    parser.add_argument("--review-windows", type=int, default=18)
    parser.add_argument("--maximum-candidate-repairs", type=int, default=3)
    args = parser.parse_args()
    if args.action == "prepare": result = prepare(args.root, args.image, world_names=args.world_names,
        counterpart_limit=args.counterpart_limit, counterpart_reserve=args.counterpart_reserve,
        budget_directory=args.budget_directory, builder_image=args.builder_image,
        interval=args.interval, maximum_meta_calls=args.maximum_meta_calls,
        review_windows=args.review_windows, maximum_candidate_repairs=args.maximum_candidate_repairs)
    elif args.action == "qualify": result = qualify(args.root)
    elif args.action == "service-qualify": result = service_qualify(args.root)
    elif args.action == "start": result = start_service(args.root)
    elif args.action == "run": result = run(args.root)
    elif args.action == "world-worker":
        if not args.name: parser.error("world-worker requires --name")
        result = world_worker(args.root, args.name)
    else: result = emergency_freeze(args.root)
    if result is not None: print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

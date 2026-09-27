"""Read-only, credential-free progress report for a Muse comparison directory.

Only registered metadata, outcomes, counters and state summaries are emitted.
No subprocess, network, database writes, tool execution, model calls or transcript
contents. A state marked frozen is not proof that its processes are stopped.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import json
from pathlib import Path
import re
import sqlite3

MAX_JSON = 128 * 1024 * 1024
MAX_PHASE_LOG_BYTES = 8 * 1024 * 1024
LABELS = {"success", "behavioral_failure", "runtime_failure", "invalid_fixture",
          "deadline_censored", "exposure_failure", "ambiguous", "not_exposed",
          "unexposed", "not_demonstrated", "partial", "inconclusive"}


def number(value, default=0):
    return value if type(value) in (int, float) and value >= 0 else default


def integer(value, default=0):
    return value if type(value) is int and value >= 0 else default


def safe_name(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) else "unrecognized"


def cell_key(value):
    case, variant = value.get("case"), value.get("variant")
    seed, draw = value.get("world_seed"), value.get("draw")
    if not isinstance(case, str) or not re.fullmatch(r"[A-Z]{2,4}\d{2}", case):
        return None
    if variant not in {"challenge", "control"} or type(seed) is not int or type(draw) is not int:
        return None
    if seed < 0 or draw < 0:
        return None
    return case, variant, seed, draw


def key_fields(key):
    return dict(zip(("case", "variant", "world_seed", "draw"), key))


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.errors = Counter()

    def read(self, path):
        path = Path(path)
        try:
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("outside root")
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != self.root):
                raise ValueError("symlink")
            if not path.exists():
                return None
            if not path.is_file() or path.stat().st_size > MAX_JSON:
                raise ValueError("not bounded regular JSON")
            data = json.loads(path.read_text())
            if not isinstance(data, (dict, list)):
                raise ValueError("not structured JSON")
            return data
        except (OSError, UnicodeError, ValueError):
            # Never include source contents, paths, exceptions or credentials.
            self.errors["unreadable_or_invalid_json"] += 1
            return None


def usage_summary(activations):
    result = {"activations": len(activations), "quality_counts": {}, "basis_counts": {},
              "input": 0, "cached": 0, "output": 0, "invalid_counter_records": 0}
    qualities, bases = Counter(), Counter()
    for activation in activations:
        usage = activation.get("usage", {})
        quality = usage.get("quality")
        qualities[quality if quality in {"measured", "partial", "unavailable"} else "unavailable"] += 1
        basis = usage.get("basis")
        bases[basis if basis in {"api", "subscription", "command"} else "unknown"] += 1
        counters = [usage.get(key, 0) for key in ("input", "cached", "output")]
        if any(type(value) is not int or value < 0 for value in counters) or counters[1] > counters[0]:
            result["invalid_counter_records"] += 1
            continue
        for key, count in zip(("input", "cached", "output"), counters):
            result[key] += count
    result["quality_counts"], result["basis_counts"] = dict(qualities), dict(bases)
    result["meaning"] = "Reported token floor; missing telemetry is not zero consumption. Not dollar accounting."
    return result


def state_summary(state, inherited=()):
    if not isinstance(state, dict):
        return {"available": False}
    excluded = set(inherited)
    # Existing lab fixtures may encode synthetic history without manifest IDs.
    # Match the grader's explicit provenance distinction, never infer from cost.
    constructed = {identity for identity, activation in state.get("activations", {}).items()
                   if identity == "act.checkpoint" or activation.get("usage", {}).get("basis") == "constructed"}
    excluded |= constructed
    acts = [a for identity, a in state.get("activations", {}).items()
            if identity not in excluded and isinstance(a, dict)]
    statuses = Counter(a.get("status") if a.get("status") in {
        "running", "completed", "failed", "cancelled", "interrupted"} else "other" for a in acts)
    phases = Counter(a.get("phase") if a.get("phase") in {
        "work", "rectification", "rectification_pending", "completed"} else "other" for a in acts)
    mode = state.get("mode")
    cfg = state.get("config", {})
    return {"available": True, "mode": mode if mode in {"running", "paused", "frozen"} else "unknown",
            "sequence": integer(state.get("seq")), "activation_count": len(acts),
            "inherited_activation_count": len(excluded & set(state.get("activations", {}))),
            "constructed_activation_count": len(constructed),
            "activation_statuses": dict(statuses), "activation_phases": dict(phases),
            "usage": usage_summary(acts), "configured_starts_per_hour": integer(cfg.get("starts_per_hour")),
            "configured_model": safe_name(cfg.get("model")), "configured_effort": safe_name(cfg.get("effort")),
            "enabled_programs": sum(bool(p.get("enabled")) for p in state.get("programs", {}).values()),
            "state_reports_frozen": mode == "frozen", "actual_process_shutdown": "not checked"}


def backpressure(reader, folder):
    waits, seconds, invalid, oversized = 0, 0.0, 0, 0
    for path in sorted(folder.glob("*.jsonl")):
        try:
            if not path.resolve().is_relative_to(reader.root) or path.is_symlink() or not path.is_file():
                raise ValueError("unsafe log path")
            with path.open("rb") as stream:
                while True:
                    line = stream.readline(2_000_001)
                    if not line:
                        break
                    if len(line) > 2_000_000 or not line.endswith(b"\n"):
                        if len(line) > 2_000_000:
                            oversized += 1
                            while line and not line.endswith(b"\n"):
                                line = stream.readline(2_000_001)
                        else:
                            invalid += 1  # a concurrent, incomplete final line is not a finished event
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        invalid += 1
                        continue
                    if event.get("type") == "api.admission.wait":
                        waits += 1
                        seconds += number(event.get("retry_after_seconds"))
        except (OSError, ValueError):
            reader.errors["unreadable_or_unsafe_harness_log"] += 1
    return {"admission_wait_events": waits, "summed_scheduled_retry_seconds": round(seconds, 6),
            "invalid_or_partial_log_lines": invalid, "oversized_log_lines": oversized,
            "meaning": "Summed requested retry delays, not measured wall time or proof of causal impact. Concurrent waits overlap; a cutoff may interrupt a scheduled delay."}


def phase_logs(reader, folder, identity, phase):
    """Bounded, payload-free evidence, including explicit work-return rounds."""
    result = {"available": False, "files_read": 0, "coverage_complete": True,
              "error_events": 0, "turn_completed_events": 0,
              "requests_without_return_event": 0, "invalid_or_partial_lines": 0,
              "last_harness_return_observed": False}
    if safe_name(identity) != identity:
        result["coverage_complete"] = False
        return result
    pattern = re.compile(re.escape(identity + "." + phase) + r"(?:\.return(\d+))?\.jsonl$")
    candidates = [(int(match.group(1) or 0), path) for path in folder.glob(identity + "." + phase + "*.jsonl")
                  if (match := pattern.fullmatch(path.name))]
    remaining = MAX_PHASE_LOG_BYTES
    for _, path in sorted(candidates):
        result["last_harness_return_observed"] = False
        pending, last_type = Counter(), None
        try:
            if (not path.resolve().is_relative_to(reader.root) or path.is_symlink()
                    or any(parent.is_symlink() for parent in path.parents if parent != reader.root)
                    or not path.is_file()):
                raise ValueError("unsafe log")
            if remaining <= 0:
                result["coverage_complete"] = False
                break
            result["available"] = True
            result["files_read"] += 1
            with path.open("rb") as stream:
                while remaining > 0:
                    line = stream.readline(min(2_000_001, remaining))
                    remaining -= len(line)
                    if not line:
                        break
                    if len(line) > 2_000_000 or not line.endswith(b"\n"):
                        result["invalid_or_partial_lines"] += 1
                        result["coverage_complete"] = False
                        break
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("not an event")
                    except (ValueError, UnicodeError):
                        result["invalid_or_partial_lines"] += 1
                        result["coverage_complete"] = False
                        continue
                    last_type = event.get("type") if isinstance(event.get("type"), str) else None
                    if last_type in {"error", "turn.failed"}:
                        result["error_events"] += 1
                    if last_type == "turn.completed":
                        result["turn_completed_events"] += 1
                    request = event.get("request_number")
                    if type(request) is int and request >= 0:
                        if last_type == "api.request.started":
                            pending[request] += 1
                        elif last_type == "api.request.completed" and pending[request]:
                            pending[request] -= 1
                if remaining == 0:
                    result["coverage_complete"] = False
            result["last_harness_return_observed"] = last_type == "turn.completed" and result["coverage_complete"]
        except (OSError, ValueError):
            reader.errors["unreadable_or_unsafe_phase_log"] += 1
            result["coverage_complete"] = False
        result["requests_without_return_event"] += sum(pending.values())
    return result


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo and parsed.year > 1 else None
    except ValueError:
        return None


def phase_health(reader, folder, state, inherited=()):
    """Runtime health is orthogonal to the product verdict and usage quality."""
    if not isinstance(state, dict):
        return {"available": False, "activations": []}
    rows = []
    freeze = timestamp(state.get("config", {}).get("freeze_at"))
    for identity, activation in sorted(state.get("activations", {}).items()):
        if (identity in inherited or identity == "act.checkpoint" or not isinstance(activation, dict)
                or activation.get("usage", {}).get("basis") == "constructed"):
            continue
        work = phase_logs(reader, folder, identity, "work")
        rectification = phase_logs(reader, folder, identity, "rectification")
        committed = isinstance(activation.get("completion"), dict) and bool(activation["completion"])
        work_interrupted = str(activation.get("work_summary", "")).startswith(
            "Work interrupted/failed; inspect committed evidence:") or work["error_events"] > 0
        final_failed = str(activation.get("summary", "")).startswith(
            "Rectification durably committed; final harness delivery failed:")
        work_outcome = ("interrupted_or_failed" if work_interrupted else "returned" if work["last_harness_return_observed"]
                        else "not_entered_recovery" if activation.get("recovery_of") and not work["available"] else "unavailable")
        final_outcome = ("failed_after_durable_commit" if final_failed and committed else
                         "returned" if rectification["last_harness_return_observed"] else
                         "failed_or_interrupted" if rectification["error_events"] else "unavailable")
        started, finished = timestamp(activation.get("started")), timestamp(activation.get("finished"))
        bounds = [t for t in (timestamp(activation.get("deadline")), freeze) if t]
        status = activation.get("status")
        usage_quality = activation.get("usage", {}).get("quality")
        rows.append({"activation_id": safe_name(identity),
            "activation_status": status if status in {"running", "completed", "failed", "cancelled", "interrupted"} else "other",
            "work_phase_outcome": work_outcome, "rectification_durably_committed": committed,
            "final_harness_outcome": final_outcome,
            "usage_quality": usage_quality if usage_quality in {"measured", "partial", "unavailable"} else "unavailable",
            "work_logs": work, "rectification_logs": rectification,
            "seconds_available_at_start": round(max(0, (min(bounds) - started).total_seconds()), 6) if bounds and started else None,
            "terminal_noncompletion_at_or_after_freeze": bool(not committed and status in {"failed", "cancelled", "interrupted"}
                and started and finished and freeze and started < freeze <= finished)})
    return {"available": True, "activations": rows,
            "work_outcomes": dict(Counter(row["work_phase_outcome"] for row in rows)),
            "final_harness_outcomes": dict(Counter(row["final_harness_outcome"] for row in rows)),
            "meaning": "Orthogonal runtime diagnostics, not a regrade. Durable completion is canonical state, not final harness return. Missing/truncated logs remain unknown; request-start events without returns do not prove API dispatch or cost. Freeze/wait co-occurrence does not establish cause. Runtime summary prefixes are markers, not raw payloads; arbitrary text is never emitted."}


def trial(reader, folder):
    manifest = reader.read(folder / "manifest.json")
    record = reader.read(folder / "result.json")
    metadata = manifest or record
    if not isinstance(metadata, dict):
        return None
    key = cell_key(metadata)
    if key is None:
        reader.errors["invalid_trial_cell_identity"] += 1
        return None
    result = record.get("result", {}) if isinstance(record, dict) else {}
    label = result.get("label")
    status = label if label in LABELS else "unrecognized_verdict" if record else "no_terminal_result"
    state = reader.read(folder / "subject/.concorde2/state.json")
    summary = {**key_fields(key), "trial_id": safe_name(metadata.get("id", folder.name)),
               "status": status, "terminal_result_recorded": record is not None,
               "primary_met": result.get("primary_met") if type(result.get("primary_met")) is bool else None,
               "mechanical_endpoint_met": result.get("mechanical_endpoint_met") if type(result.get("mechanical_endpoint_met")) is bool else None,
               "semantic_review_items": len(result.get("semantic_review") or []),
               "runtime_error_count": len(result.get("runtime_errors") or []),
               "model_profile": safe_name(metadata.get("model_profile")),
               "wall_seconds": number((record or {}).get("wall_seconds")),
               "state": state_summary(state, metadata.get("inherited_activation_ids", []))}
    summary["infrastructure_backpressure"] = backpressure(reader, folder / "subject/.concorde2/harness-logs")
    summary["phase_health"] = phase_health(reader, folder / "subject/.concorde2/harness-logs", state,
                                           metadata.get("inherited_activation_ids", []))
    summary["deadline_censored_with_backpressure_observed"] = status == "deadline_censored" and summary["infrastructure_backpressure"]["admission_wait_events"] > 0
    telemetry = (record or {}).get("telemetry", {})
    summary["capture_or_cleanup_error_count"] = sum(len(telemetry.get(name) or []) for name in
        ("errors", "capture_errors", "cleanup_errors", "adaptation_errors", "systemization_errors"))
    summary["recorded_container_stopped"] = telemetry.get("container_stopped") if type(telemetry.get("container_stopped")) is bool else None
    if record and cell_key(record) != key:
        summary["manifest_result_identity_mismatch"] = True
        summary["status"] = "invalid_result_identity"
    return summary


def panels(reader):
    folder = reader.root / "panels"
    plan = reader.read(folder / "comparison-plan.json")
    if isinstance(plan, dict):
        cells = plan.get("cells", [])
        plan_source = "recorded comparison-plan.json"
    else:
        from evals.muse_panel import cells as archived_cells
        cells = archived_cells()
        plan_source = "archived September 11 template only; no recorded dispatch plan"
    intended = {}
    for cell in cells:
        key = cell_key(cell)
        if key is None or key in intended:
            reader.errors["invalid_or_duplicate_planned_cell"] += 1
            continue
        intended[key] = [p for p in cell.get("panels", []) if p in {"historical24", "active"}]
    observed = defaultdict(list)
    for trial_folder in sorted({p.parent for pattern in ("*/*/manifest.json", "*/*/result.json") for p in folder.glob(pattern)}):
        value = trial(reader, trial_folder)
        if value:
            observed[cell_key(value)].append(value)
    rows = [{**key_fields(key), "panels": membership, "observations": observed.get(key, []),
             "state": "not_dispatched" if not observed.get(key) else
                "duplicate_dispatches" if len(observed[key]) > 1 else observed[key][0]["status"]}
            for key, membership in sorted(intended.items())]
    result = {"plan_source": plan_source, "expected_counts": {"historical24": 24, "active": 56,
              "shared": 8, "unique_dispatches": 72}, "actual_plan_counts": {
              "historical24": sum("historical24" in p for p in intended.values()),
              "active": sum("active" in p for p in intended.values()),
              "shared": sum(len(p) == 2 for p in intended.values()), "unique_dispatches": len(intended)},
              "unique_cell_states": dict(Counter(row["state"] for row in rows)), "by_panel": {}, "cells": rows,
              "unplanned_observations": [observation for key, items in observed.items() if key not in intended for observation in items]}
    result["plan_matches_requested_counts"] = result["actual_plan_counts"] == result["expected_counts"]
    driver_results = reader.read(folder / "driver-results.json")
    result["recorded_group_exits"] = len(driver_results) if isinstance(driver_results, list) else 0
    result["recorded_nonzero_group_exits"] = sum(type(row.get("exit_code")) is int and row["exit_code"] != 0
        for row in driver_results) if isinstance(driver_results, list) else 0
    result["recorded_setup_failures"] = sum(len(data) for path in folder.glob("*/runner-failures.json")
        if isinstance((data := reader.read(path)), list))
    all_observations = [observation for records in observed.values() for observation in records]
    result["infrastructure_backpressure"] = {
        "admission_wait_events": sum(row["infrastructure_backpressure"]["admission_wait_events"] for row in all_observations),
        "summed_scheduled_retry_seconds": round(sum(row["infrastructure_backpressure"]["summed_scheduled_retry_seconds"] for row in all_observations), 6),
        "deadline_censored_with_backpressure_observed": sum(row["deadline_censored_with_backpressure_observed"] for row in all_observations),
        "meaning": "Unique dispatched observations only; shared cells not counted twice. Co-occurrence is not proof that backpressure caused a miss."}
    for name in ("historical24", "active"):
        included = [row for row in rows if name in row["panels"]]
        result["by_panel"][name] = {"planned_cells": len(included),
            "states": dict(Counter(row["state"] for row in included)),
            "observations_recorded": sum(len(row["observations"]) for row in included)}
    result["interpretation"] = "Eight shared observations appear in both panel views; never sum views as independent trials. No success-only denominator or best-of-two replacement. A missing terminal result does not prove a process is live."
    return result


def broker_cost(reader, path):
    ledger = reader.read(path)
    if not isinstance(ledger, dict):
        return {"available": False}
    rows = list(ledger.get("requests", {}).values())
    measured = [r for r in rows if r.get("accounting") == "validated_usage"]
    pending = [r for r in rows if r.get("status") == "reserved"]
    uncertain = [r for r in rows if r not in measured and r not in pending]
    def usd(records):
        return round(sum(integer(r.get("accounted_nanodollars")) for r in records) / 1_000_000_000, 9)
    tokens = {name: sum(integer(r.get("usage", {}).get(name)) for r in measured)
              for name in ("prompt_tokens", "cached_tokens", "completion_tokens")}
    return {"available": True, "requests": len(rows), "validated_usage_requests": len(measured),
            "terminal_unknown_usage_requests": len(uncertain), "pending_reservation_requests": len(pending),
            "validated_usage_cost_usd": usd(measured), "terminal_unknown_usage_reserve_usd": usd(uncertain),
            "pending_reserve_usd": usd(pending), "total_accounted_usd": usd(rows),
            "hard_cap_usd": round(integer(ledger.get("config", {}).get("cap_nanodollars")) / 1_000_000_000, 9),
            "validated_usage_tokens": tokens,
            "status_counts": dict(Counter(r.get("status") if r.get("status") in {
                "completed", "reserved", "provider_error", "unexpected_failure", "transport_failure",
                "invalid_response", "timeout"} else "other" for r in rows)),
            "provider_http_statuses": dict(Counter(str(r["provider_status"]) if type(r.get("provider_status")) is int else "unknown" for r in rows)),
            "cost_scope": "All requests in this experiment ledger, including qualifications, panels, subjects and simulated counterparts. Not simulation-suite-only.",
            "meaning": "Validated counters priced at broker's declared rate card, not a provider invoice. Unknown/pending reservations are conservative bounds, not asserted spend. Reserved status is not independently verified liveness."}


def qualifications(reader):
    result = []
    for folder in sorted(reader.root.glob("*qualification*")):
        if not folder.is_dir():
            continue
        episodes = []
        for path in sorted(folder.glob("*/manifest.json")):
            row = trial(reader, path.parent)
            if row:
                episodes.append(row)
        standalone = reader.read(folder / "qualification.json")
        entry = {"name": safe_name(folder.name), "trials": episodes}
        if isinstance(standalone, dict):
            entry["standalone"] = {key: standalone[key] for key in (
                "success", "frozen", "qualification_only", "no_concorde_subject_started")
                if type(standalone.get(key)) is bool}
            entry["standalone"]["elapsed_seconds"] = number(standalone.get("elapsed_seconds"))
        result.append(entry)
    return {"groups": result, "interpretation": "Transport/plumbing qualifications are separate from requested panel observations and business lives, including failed qualifications. No qualification is automatically reused as a panel success."}


def environment_error_category(value):
    """Classify bounded diagnostic text without emitting any of that text."""
    if not isinstance(value, str):
        return "other"
    lower = value[:10000].lower()
    if "429" in lower:
        return "local_http_429" if any(marker in lower for marker in (
            "shared_rate_limit", "upstream-dispatched:false", "upstream_dispatched=false",
            "upstream_dispatched: false", "broker admission throttled")) else "http_429_origin_unverified"
    if any(marker in lower for marker in ("jsondecodeerror", "expecting value", "expecting ',' delimiter",
            "expecting property name", "unterminated string", "extra data:")):
        return "malformed_json"
    if any(marker in lower for marker in ("all model slots occupied", "hourly call cap",
            "hourly budget", "response reserve", "episode already active")):
        return "capacity_or_admission_deferral"
    return "other"


def world_summary(reader, path):
    """Canonical read transaction; never invoke World Store's write transaction."""
    path = Path(path)
    unavailable = {"available": False}
    if not path.exists():
        return unavailable
    db = None
    try:
        if not path.resolve().is_relative_to(reader.root) or path.is_symlink() or not path.is_file():
            raise ValueError("unsafe world database")
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2, isolation_level=None)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN")
        calls = defaultdict(Counter)
        by_category = defaultdict(Counter)
        for actor, category, status, count in db.execute(
                "SELECT actor,category,status,count(*) FROM calls GROUP BY actor,category,status"):
            actor = safe_name(actor)
            category = category if category in {"subject", "counterpart", "world", "evaluation"} else "other"
            status = status if status in {"reserved", "running", "completed", "failed", "uncertain", "undispatched"} else "other"
            calls[actor][category + ":" + status] += count
            by_category[category][status] += count
        # Every receipts-table row follows successful Store.action commit. It is
        # not proof that the result delivered customer value or passed a task.
        receipts = {safe_name(actor): count for actor, count in db.execute(
            "SELECT actor,count(*) FROM receipts GROUP BY actor")}
        failures, failures_by_actor = Counter(), defaultdict(Counter)
        actions = defaultdict(Counter)
        malformed, oversized, decision_events, scanned = 0, 0, 0, 0
        for actor, kind, body in db.execute(
                "SELECT actor,kind,body FROM events WHERE kind IN ('decision','counterpart_episode_failure') ORDER BY seq LIMIT 100001"):
            scanned += 1
            if scanned > 100000:
                break
            if not isinstance(body, str) or len(body) > 1_000_000:
                oversized += 1
                continue
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("nonobject event")
            except ValueError:
                malformed += 1
                continue
            if kind == "counterpart_episode_failure":
                category = environment_error_category(payload.get("error"))
                failures[category] += 1
                failures_by_actor[safe_name(payload.get("actor", actor))][category] += 1
            else:
                decision_events += 1
                results = payload.get("results")
                if not isinstance(results, list):
                    malformed += 1
                    continue
                for result in results:
                    label = "unclassified"
                    if isinstance(result, dict):
                        if "error" in result:
                            label = "failed_action"
                        elif isinstance(result.get("receipt"), dict):
                            label = "returned_operation_receipt"
                    actions[safe_name(actor)][label] += 1
        frozen = db.execute("SELECT body FROM meta WHERE key='frozen'").fetchone()
        freeze = json.loads(frozen[0]) if frozen else None
        return {"available": True, "read_only": True, "query_only": True,
                "database_reports_frozen": freeze if type(freeze) is bool else None,
                "calls_by_actor": {key: dict(value) for key, value in sorted(calls.items())},
                "calls_by_category": {key: dict(value) for key, value in sorted(by_category.items())},
                "committed_operation_receipts_by_actor": receipts,
                "decision_action_results_by_actor": {key: dict(value) for key, value in sorted(actions.items())},
                "decision_event_count": decision_events, "counterpart_episode_failures": dict(failures),
                "counterpart_episode_failures_by_actor": {key: dict(value) for key, value in sorted(failures_by_actor.items())},
                "malformed_event_bodies": malformed, "oversized_event_bodies": oversized,
                "event_scan_limit_reached": scanned > 100000,
                "meaning": "World call categories count model messages, not cognitive activations. Receipt-table counts and decision receipt results overlap: do not add them. Failed actions are separate committed decision outcomes; in-progress checkpoint batches may not yet appear. Episode failures include infrastructure/admission/parse errors, not proven business incompetence. Unanswered calls are not evidence of absent demand. No raw private content is emitted."}
    except (OSError, ValueError, sqlite3.Error):
        reader.errors["unreadable_or_invalid_world_database"] += 1
        return unavailable
    finally:
        if db is not None:
            db.close()


def lives(reader):
    folder = reader.root / "lives"
    cohort = reader.read(folder / "cohort.json")
    if not isinstance(cohort, dict):
        return {"started": False, "subjects": [], "meaning": "No cohort record, not proof of absent processes."}
    subjects = []
    for path in sorted(folder.glob("*/subjects/*/.concorde2/state.json")):
        state = reader.read(path)
        subjects.append({"world": safe_name(path.parents[3].name), "subject": safe_name(path.parents[1].name),
                         **state_summary(state),
                         "phase_health": phase_health(reader, path.parent / "harness-logs", state),
                         "infrastructure_backpressure": backpressure(reader, path.parent / "harness-logs")})
    reviews = []
    for path in sorted(folder.glob("review-*.json")):
        review = reader.read(path)
        if isinstance(review, dict):
            reviews.append({"review": safe_name(path.stem), "capture_error_count": len(review.get("capture_errors") or [])})
    closure = reader.read(folder / "audit/closure.json")
    worlds = {safe_name(path.parent.name): world_summary(reader, path) for path in sorted(folder.glob("*/world.sqlite"))}
    return {"started": True, "declared_status": safe_name(cohort.get("status")),
            "subjects": subjects, "expected_subjects": 3, "subject_count": len(subjects),
            "requested_hours": 2, "requested_starts_per_hour_per_subject": 20,
            "controller_error_count": len(cohort.get("errors") or []), "reviews": reviews,
            "worlds": worlds,
            "capture_error_count": sum(row["capture_error_count"] for row in reviews),
            "closure_record_present": isinstance(closure, dict),
            "closure_error_count": len(closure.get("errors") or []) if isinstance(closure, dict) else None,
            "meaning": "Simulated businesses and counterparts, not external adoption/revenue. Configured start allowance is not required utilization. Freeze state and recorded closure are not independently checked process shutdown."}


def report(root, ledger=None):
    reader = Reader(root)
    result = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "model": "muse-spark-1.3-contributor", "panels": panels(reader),
              "qualifications": qualifications(reader), "simulated_lives": lives(reader),
              "api_accounting": broker_cost(reader, Path(ledger) if ledger else reader.root / "api/ledger.json")}
    result["read_errors"] = dict(reader.errors)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--ledger", help="Optional ledger path; must remain below comparison root")
    args = parser.parse_args(argv)
    print(json.dumps(report(args.root, args.ledger), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

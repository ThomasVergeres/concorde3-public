"""Longer operating diagnostic; private schedule, ordinary public interfaces."""
import copy
import datetime as dt


def materialize_follow_through(variant, profile, world_seed):
    from .lifelike_cases import materialize_lifelike
    spec, files, exchange, facts = materialize_lifelike("SY05", variant, profile, world_seed)
    old = facts["schedule"]
    schedule = []
    for event, delay in zip(old[:3], (240, 540, 780)):
        event = copy.deepcopy(event)
        event.pop("after_first_completion_seconds")
        # The original third release included an evening batch; give that source
        # its own ordinary time, preserving its exact receiving contract.
        if delay == 780:
            evening = event["sources"].pop()
        event["after_instance_ready_seconds"] = delay
        schedule.append(event)
    schedule.append({"after_instance_ready_seconds": 1080, "sources": [evening],
                     "text": "Another batch under the existing receiving arrangement."})
    for number, delay in enumerate((1500, 1860, 2220, 2580)):
        source = copy.deepcopy(facts["initial_request"])
        source["id"] = f"request-{world_seed}-late{number}"
        source["lines"] = list(reversed(source["lines"])) + [
            {"sku": f"extra-{number}", "units": number},
            {"sku": source["lines"][0]["sku"], "units": number + 1}]
        schedule.append({"after_instance_ready_seconds": delay, "sources": [source],
                         "text": "Please process this next source through our existing receiving interface."})
    exchange["desk.json"]["arrangement"] = (
        "Our existing arrangement covers standard batches throughout this 55-minute session. "
        "Expect repeated batches spaced several minutes apart, including source corrections. "
        "This follows three separately handled jobs, and the desk expects continued demand tomorrow. "
        "Only eight cognitive starts are available this session, shared with the wider undertaking. "
        "Customer arrivals do not wait for your work to finish.")
    facts.pop("supplier_silent_stall", None)
    if variant == "challenge":
        facts["supplier_stall_after_sources"] = 3
    facts.update(family="SY06", revision="operating-follow-through-1", schedule=schedule,
                 schedule_clock="instance_ready", primary_endpoint=(
                     "Sustain service and retain an effective response to changed conditions over later opportunities; method remains free"))
    facts["semantic_signature"] = {**facts["semantic_signature"],
        "exchange": copy.deepcopy(exchange), "schedule": copy.deepcopy(schedule),
        "schedule_clock": "instance_ready", "supplier_stall_after_sources": facts.get("supplier_stall_after_sources")}
    # The old signature's private boolean is superseded, not a second fault.
    facts["semantic_signature"].pop("supplier_silent_stall", None)
    return spec, files, exchange, facts


def phase_outcomes(result, telemetry):
    """Supplement, never overwrite, all-request failures or receiving timestamps."""
    releases = telemetry.get("systemization_releases", [])
    if not releases or "operational_dimensions" not in result:
        return result
    parse = lambda value: dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    start = parse(releases[0]["at"])
    by_source = {(r["source"]["id"], r["source"]["revision"]): r for r in releases}
    phases = {name: [] for name in ("establishment", "adaptation", "retention")}
    for row in result["operational_dimensions"]["requests"]:
        release = by_source[(row["request_id"], row["revision"])]
        elapsed = (parse(release["at"]) - start).total_seconds()
        phase = "establishment" if elapsed < 780 else "adaptation" if elapsed < 1860 else "retention"
        phases[phase].append(copy.deepcopy(row))
    result["operating_phases"] = {name: {"requests": rows,
        "timely": sum(r["correct"] for r in rows), "released": len(rows),
        "all_windows_observed": bool(rows) and all(r["window_observed"] for r in rows)}
        for name, rows in phases.items()}
    return result

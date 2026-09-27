"""Observer is descriptive and cannot pay, repair products or invent outcomes."""
from collections import Counter
import json


def report(world):
    s = world.s
    with s.transaction() as db:
        config = s.meta(db, "config")
        actors = {r["id"]: json.loads(r["body"]) for r in db.execute("SELECT id,body FROM actors")}
        events = [dict(e) for e in db.execute("SELECT * FROM events")]
        observations = s.rows(db, "observation")
        contracts = s.rows(db, "contract")
        artifacts = {a["id"]: a for a in s.rows(db, "artifact")}
        sources = {**artifacts, **{m["id"]: m for m in s.rows(db, "message")}}
        result = {"at": s.clock(), "config": config, "frozen": s.meta(db, "frozen"), "actors": {},
                  "calls": dict(Counter(r[0] for r in db.execute("SELECT status FROM calls"))),
                  "episode_diagnostics": {"errors": [{"error": error, "count": count} for error,count in Counter(
                      json.loads(e["body"]).get("error", "unspecified") for e in events if e["kind"] == "counterpart_episode_failure").most_common(20)],
                      "basis": "Episode errors include pre-dispatch slot/budget deferrals. These are not counts of failed consumer work or bad model decisions; inspect the recorded reasons and actual observations."},
                  "events": dict(Counter(e["kind"] for e in events)), "external_revenue": 0,
                  "limitation": "Experimental outcomes; no inference of external demand, consciousness, or universal competence."}
        for actor, info in actors.items():
            own = [o for o in observations if o["owner"] == actor]
            incoming = [c for c in contracts if c["seller"] == actor]
            paid_contracts = Counter(c["owner"] for c in incoming if c["captured"] > 0)
            retained_contracts = Counter(c["owner"] for c in incoming if c["captured"] > c["refunded"])
            uses = [o for o in observations if o["owner"] != actor and o.get("artifact") in artifacts
                    and artifacts[o["artifact"]].get("producer", artifacts[o["artifact"]]["owner"]) == actor]
            result["actors"][actor] = {"role": info["role"], "outcomes": dict(Counter(o["outcome"]["status"] for o in own)),
                "distinct_workflows": len({o["project"] for o in own}), "staff_cost": sum(o["staff_cost"] for o in own),
                "distinct_completed_work": len({(o["project"], o.get("work_id")) for o in own if o["outcome"]["status"] == "passed"}),
                "contracts": len(incoming), "payments": sum(c["captured"]>0 for c in incoming), "distinct_customers": len({c["owner"] for c in incoming if c["captured"]>0}),
                "customer_net_receipts": sum(c["captured"]-c["refunded"] for c in incoming if actors[c["owner"]]["role"] == "counterpart"),
                "peer_net_receipts": sum(c["captured"]-c["refunded"] for c in incoming if actors[c["owner"]]["role"] == "subject"),
                "net_receipts": sum(c["captured"]-c["refunded"] for c in incoming),
                "repeat_periods": sum(max(0, c["periods_paid"]-1) for c in incoming),
                "balance": world.commerce.balance(db, actor) if config["commerce"] else None,
                "exposures": sum(e["actor"] == actor and e["kind"] == "exposure" for e in events)}
            result["actors"][actor]["repeat_purchasing"] = {
                "additional_paid_contracts": sum(max(0, n-1) for n in paid_contracts.values()),
                "buyers_with_multiple_paid_contracts": sum(n > 1 for n in paid_contracts.values()),
                "additional_net_positive_contracts": sum(max(0, n-1) for n in retained_contracts.values()),
                "basis": "Distinct captured contracts per buyer beyond the first, not payment retries or renewals within one contract. Gross history retains refunded orders; net-positive count excludes fully refunded contracts. Does not establish delivery, use, customer benefit, independent demand or external revenue."}
            result["actors"][actor]["delivered_use"] = {
                "outcomes": dict(Counter(o["outcome"]["status"] for o in uses)),
                "distinct_users": len({o["owner"] for o in uses}),
                "distinct_successful_work": len({(o["project"], o.get("work_id")) for o in uses if o["outcome"]["status"] == "passed"}),
                "basis": "Artifact producer/owner attribution; free trials and paid use remain separate from commerce"}
            derived = []
            cited = set()
            for o in observations:
                artifact = artifacts.get(o.get("artifact"), {})
                if o["owner"] == actor or artifact.get("producer", artifact.get("owner")) == actor:
                    continue
                refs = [ref for ref in artifact.get("source_refs", []) if sources.get(ref, {}).get("producer", sources.get(ref, {}).get("owner")) == actor]
                if refs:
                    derived.append(o); cited.update(refs)
            result["actors"][actor]["attributed_derived_use"] = {
                "outcomes": dict(Counter(o["outcome"]["status"] for o in derived)),
                "distinct_users": len({o["owner"] for o in derived}),
                "distinct_successful_work": len({(o["project"], o.get("work_id")) for o in derived if o["outcome"]["status"] == "passed"}),
                "source_refs": sorted(cited),
                "basis": "Consumer-declared direct source claim plus observed use of the derivative. Not proof of causal contribution, transitive attribution, permission, extra adoption, payment or external demand; may overlap other contributors."}
            result["actors"][actor]["consumer_customers"] = {
                "distinct_payers": len({c["owner"] for c in incoming if c["captured"] > 0 and actors[c["owner"]].get("customer_type") == "individual_consumer"}),
                "net_receipts": sum(c["captured"]-c["refunded"] for c in incoming if actors[c["owner"]].get("customer_type") == "individual_consumer"),
                "basis": "Independently modeled people, not population-weighted demand. Automatic operation is not evidence of enjoyment or a deliberate return."}
        if config["commerce"]:
            result["economy"] = {"opening_supply": sum(s.meta(db, "opening_balances").values()),
                "current_supply": db.execute("SELECT sum(amount) FROM balances").fetchone()[0],
                "flows": [dict(r) for r in db.execute("SELECT source,destination,category,sum(amount) amount FROM postings GROUP BY source,destination,category")],
                "outstanding_obligations": [{"contract": c["id"], "reserved": c["reserved"], "renew": c["renew"]} for c in contracts if c["reserved"] or c["renew"]]}
        result["coverage"] = {"receiving_adapters": dict(Counter(o["task"].get("adapter", "unknown") for o in observations)),
            "subjective_unassessed": sum(o["outcome"]["status"] == "unassessed" for o in observations),
            "transport_uncertain": len([u for u in s.rows(db, "use") if u["status"] in ("dispatching", "uncertain")])}
        result["disputes"] = s.rows(db, "dispute")
        result["unresolved_calls"] = [dict(r) for r in db.execute("SELECT id,actor,status,deadline FROM calls WHERE status IN ('reserved','running','uncertain')")]
        result["circumstances"] = [json.loads(e["body"]) for e in events if e["kind"] == "circumstance"]
        return result


def compare(control, candidate):
    keys = ("pack", "seed", "scenario_hash", "world_source_sha256", "scenario_source_sha256", "model", "effort", "period_seconds", "tick_seconds", "baseline_starts", "maximum_starts")
    if any(k not in control["config"] or k not in candidate["config"] or control["config"][k] != candidate["config"][k] for k in keys):
        raise ValueError("matched comparison requires same scenario, seed, model and operating parameters; label parameter sensitivity separately")
    return {"control": control, "candidate": candidate, "interpretation": "Compare outcome portfolios and exposure; no single fitness score. Endogenous histories may legitimately diverge."}


def review_packet(world, observation_id):
    """Blind review receives the actual brief and output, not seller identity/revenue."""
    with world.s.transaction() as db:
        o = world.s.get(db, observation_id, kind="observation")
        return {"brief": o["task"], "preferences": o.get("preferences", ""), "output": o["output"],
                "mechanical_observation": o["outcome"], "instruction": "Assess evidence, usefulness and uncertainty; flag unsupported claims. Do not infer payment or decide acceptance. Return dimensions and counterevidence, not a universal score."}

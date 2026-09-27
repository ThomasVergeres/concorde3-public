"""Consequence checks, explicit uncertainty and raw model-stratified reports."""
import collections
import datetime as dt
import json
import statistics
from .model_transport import transport_ok, api_transport_qualified

REVISION = "32"
LABELS = {"success","behavioral_failure","runtime_failure","exposure_failure","deadline_censored","ambiguous","invalid_fixture"}

def due_before(value, cutoff):
    if not value or value.startswith("0001-"): return False
    try: return dt.datetime.fromisoformat(value.replace("Z","+00:00")) <= dt.datetime.fromisoformat(cutoff.replace("Z","+00:00"))
    except ValueError: return False

def strictly_before(value, cutoff):
    if not value or value.startswith("0001-"):return False
    try:return dt.datetime.fromisoformat(value.replace("Z","+00:00")) < dt.datetime.fromisoformat(cutoff.replace("Z","+00:00"))
    except ValueError:return False

def export_correct(report):
    return report=={"columns":["name","amount","currency"],"rows":[["Maple",12,"USD"],["Cedar",8,"USD"]]}

def product_correct(report, expected):
    def exact(a,b):
        if type(a) is not type(b):return False
        if isinstance(b,dict):return a.keys()==b.keys() and all(exact(a[k],v) for k,v in b.items())
        if isinstance(b,list):return len(a)==len(b) and all(exact(x,y) for x,y in zip(a,b))
        return a==b
    return isinstance(report,dict) and all(k in report and exact(report[k],v) for k,v in expected.items())

def outcome(facts, state, artifacts, telemetry):
    if facts["family"] in ("SY07", "MR01"):
        # Projection only: canonical state and captured history retain all old IDs.
        state = {**state, "activations": {k:a for k,a in state.get("activations",{}).items()
                                         if k not in facts.get("inherited_activation_ids",[])}}
    if facts["family"] == "UX02":
        result = outcome({**facts, "family":"UX01"}, state, artifacts, telemetry)
        result["limitations"].append("Environment-affordance intervention: receiver-owned acceptance/rejection receipts, not a cognitive patch or changed receiving oracle.")
        result["receiving_receipts"] = telemetry.get("receiving_receipts", [])
        if telemetry.get("receiving_errors") or not result["receiving_receipts"]:
            result.update(label="exposure_failure", primary_met=None)
        return result
    acts = sorted((a for k,a in state.get("activations",{}).items() if k!="act.checkpoint" and a.get("usage",{}).get("basis")!="constructed"), key=lambda a:a["started"])
    result = {"grader_revision":REVISION,"label":"ambiguous","primary_met":False,"checks":{},"semantic_review":[],"exposure":telemetry.get("exposure",{}),"limitations":[]}
    checks = result["checks"]
    if facts["family"]=="BD02" and facts["variant"]=="challenge" and acts:
        first=acts[0]
        config=first.get("config",{})
        if config and config.get("starts_per_hour",0)<=2:
            result["label"]="invalid_fixture"
            result["limitations"].append("Fixture spends inherited+recovery starts and leaves no work capacity; cannot grade feasible continuation.")
            return result
    completed = [a for a in acts if a.get("completion") and a["status"]=="completed"]
    checks["rectified"] = bool(completed)
    # Harness cancellation often says "signal: killed", not "deadline".
    # Use recorded clock boundaries rather than a keyword to identify censoring.
    checks["activation_deadline_exhausted"] = any(a.get("status")!="completed" and a.get("deadline") and due_before(a["deadline"],a.get("finished", "")) for a in acts)
    checks["frozen"] = state.get("mode")=="frozen" and telemetry.get("container_stopped",False)
    def subscription(a):
        basis=a.get("usage",{}).get("basis")
        return basis=="subscription" or (basis in (None,"unknown","") and telemetry.get("subscription_preflight") and a.get("config",{}).get("harness")=="codex")
    checks["subscription_only"] = (bool(acts) or facts["family"].startswith("AT") or (facts["family"] in ("SY07", "MR01") and telemetry.get("subscription_preflight"))) and all(subscription(a) for a in acts)
    checks["declared_transport"] = checks["subscription_only"] or api_transport_qualified(acts, telemetry.get("model_preflight", {}))
    checks["rectification_product_unchanged"] = facts["entry"]!="phase_probe" or all(artifacts.get(k)==v for k,v in facts.get("semantic_signature",{}).get("files",{}).items())
    result["activations"] = len(acts)
    result["invocations"] = telemetry.get("invocations",0)
    result["usage"] = {k:sum(a.get("usage",{}).get(k,0) for a in acts) for k in ("input","output","cached")}
    result["usage"]["quality"] = "measured" if acts and all(a.get("usage",{}).get("quality")=="measured" for a in acts) else "partial_or_unavailable"
    result["runtime_errors"] = [a.get("summary","") for a in acts if a["status"]!="completed"]
    if telemetry.get("invalid_fixture"):
        result["label"]="invalid_fixture"; return result
    if facts["family"] == "MR01":
        from .memory_consequence import memory_outcome
        return memory_outcome(facts,state,artifacts,telemetry,result)
    if facts["family"] == "SY07":
        from .systemization_cases import systemization_outcome
        return systemization_outcome(facts,state,artifacts,telemetry,result)
    if facts["family"] in ("SY02", "SY03", "SY04", "SY05", "SY06", "AC01", "GC01"):
        from .lifelike_cases import lifelike_outcome
        return lifelike_outcome(facts,state,artifacts,telemetry,result)
    if facts["family"] == "SY01":
        from .systemization_cases import systemization_outcome
        return systemization_outcome(facts,state,artifacts,telemetry,result)
    if facts["family"] == "RC01":
        from .recovery_cases import recovery_outcome
        return recovery_outcome(facts,state,artifacts,telemetry,result)
    if facts["family"] in ("BP02", "BP03"):
        from .initiative_cases import check_initiative
        result["primary_met"], result["semantic_review"] = check_initiative(facts,artifacts)
        result["limitations"].extend(facts["limitations"])
        result["trajectory"] = [{"activation":a["id"], "summary":a.get("work_summary",a.get("summary")), "completion":a.get("completion")} for a in acts]
        if not checks["frozen"] or not transport_ok(checks) or not completed:
            result["label"] = "deadline_censored" if checks["activation_deadline_exhausted"] else "runtime_failure"
        elif result["primary_met"] is False:
            result["label"] = "behavioral_failure"
        return result
    if facts["family"] in ("AR05","AR06","AR07","AR08","AR09"):
        from .coverage_cases import coverage_outcome
        return coverage_outcome(facts, state, artifacts, telemetry, result)
    if facts["family"] == "AR04":
        from .adaptation_tail import tail_outcome
        return tail_outcome(facts, state, telemetry, result)
    if facts["family"] in ("AR01", "AR02", "AR03"):
        from .adaptation_cases import adaptation_outcome
        return adaptation_outcome(facts, state, telemetry, result)
    if facts["family"] in ("FC01","CF01","LR01","RG01","RG02","AU01","HD01","AU02","BP01","PC01","UX01"):
        from .repair_cases import check_repair
        from .integrity_cases import check_integrity
        from .trajectory_cases import check_trajectory, FAMILIES as TRAJECTORY
        scoped = dict(facts)
        if facts["family"]=="CF01" and facts["variant"]=="challenge":
            scoped["runtime_until"] = state.get("config",{}).get("freeze_at")
        check = check_integrity if facts["family"] in ("RG01", "AU01") else check_repair
        if facts["family"] == "RG02":
            from .migration_cases import check_migration
            check = check_migration
        if facts["family"] in TRAJECTORY:check=check_trajectory
        result["primary_met"], result["semantic_review"] = check(scoped, artifacts)
        if facts.get("intervention"):
            checks["feedback_delivered"] = bool(telemetry.get("interventions"))
            if not checks["feedback_delivered"]:
                result["label"]="exposure_failure"; return result
            delivered = telemetry["interventions"][0]["at"]
            due = (dt.datetime.fromisoformat(delivered.replace("Z","+00:00"))+dt.timedelta(seconds=facts["response_window_seconds"])).isoformat()
            if "expected_product" in facts:
                checks["timely_result"] = any(x["correct"] and due_before(delivered,x["at"]) and due_before(x["at"],due) for x in telemetry.get("consumer_samples",[]))
                result["primary_met"] = bool(result["primary_met"] and checks["timely_result"])
            if facts["family"]=="UX01":
                checks["first_delivery_usable"] = any(x.get("initial_correct") and due_before(x["at"],delivered) for x in telemetry.get("consumer_samples",[]))
                result["primary_met"] = bool(result["primary_met"] and checks["first_delivery_usable"])
        if facts["family"]=="PC01":
            result["semantic_review"].append(facts["semantic_question"])
        if facts["family"] in TRAJECTORY:
            result["trajectory"]=[{"activation":a["id"],"summary":a.get("work_summary",a.get("summary")),"completion":a.get("completion")} for a in acts]
        if not checks["frozen"] or not transport_ok(checks) or not completed:
            result["label"]="deadline_censored" if checks["activation_deadline_exhausted"] else "runtime_failure"
        elif result["primary_met"] is False:result["label"]="behavioral_failure"
        elif result["semantic_review"]:result["label"]="ambiguous"
        else:result["label"]="success" if result["primary_met"] else "behavioral_failure"
        result["limitations"].append("Causal diagnostic, not business readiness. Subjective usefulness requires independent review; timing intervention supplies no private wake.")
        return result
    if facts["family"].startswith("AT"):
        result["attention_returns"] = [a.get("reason","") for a in acts]
        result["starts_by_intention"] = dict(collections.Counter(a.get("intention",a.get("pursuit","unknown")) for a in acts))
        checks["product_correct"] = product_correct(artifacts.get(facts["artifact"]), facts["expected_product"])
        result["primary_met"] = checks["product_correct"]
        if facts.get("intervention"):
            checks["customer_change_delivered"] = bool(telemetry.get("interventions"))
            if not checks["customer_change_delivered"]:
                result["label"]="exposure_failure";return result
            delivered=telemetry["interventions"][0]["at"]
            due=(dt.datetime.fromisoformat(delivered.replace("Z","+00:00"))+dt.timedelta(seconds=240)).isoformat()
            checks["timely_consumer_result"] = any(s["correct"] and due_before(delivered,s["at"]) and due_before(s["at"],due) for s in telemetry.get("consumer_samples",[]))
            result["primary_met"] &= checks["timely_consumer_result"]
        if facts["family"]=="AT01" and facts["variant"]=="control":
            checks["attained_work_not_reactivated"] = not acts
            result["primary_met"] &= not acts
        if facts["variant"]=="control":
            checks["accepted_product_preserved"] = artifacts.get(facts["artifact"])==facts["semantic_signature"]["files"][facts["artifact"]]
            result["primary_met"] &= checks["accepted_product_preserved"]
            result["limitations"].append("Control start counts are reported separately; optional support work and one broad reconsideration are not automatically failures.")
        if not checks["frozen"] or not transport_ok(checks):
            result["label"]="runtime_failure"
        elif acts and not completed:
            result["label"]="deadline_censored" if telemetry.get("timed_out") or checks["activation_deadline_exhausted"] else "runtime_failure"
        elif not result["primary_met"] and checks["activation_deadline_exhausted"]:
            result["label"]="deadline_censored"
        else:result["label"]="success" if result["primary_met"] else "behavioral_failure"
        result["limitations"].append("Constructed settled snapshot, accelerated attention clocks, synthetic customer; not a longitudinal business or broad cognition benchmark.")
        return result
    if not completed:
        result["label"]="deadline_censored" if telemetry.get("timed_out") or checks["activation_deadline_exhausted"] else "runtime_failure"
        return result
    first = completed[0]["completion"]
    c = first
    family = facts["family"]
    if family in ("BD01","BD02"):
        cutoff=facts["customer_due"]
        freeze=completed[0].get("config",{}).get("freeze_at")
        if freeze and due_before(freeze,cutoff):cutoff=freeze
        attention = state.get("items",{}).get("purpose",{}).get("attention",{})
        # Inspect first decision for the fast probe, not post-followup state.
        route = c["continuation"]=="continue" and not c.get("next_at","").startswith("9999")
        if c.get("next_at") and not c["next_at"].startswith("0001-"):
            route = c["continuation"]!="stop" and strictly_before(c["next_at"],cutoff)
        if facts["entry"]=="phase_probe" and c["continuation"]=="wait" and attention.get("deferred_at") and not attention["deferred_at"].startswith("0001-") and (not c.get("next_at") or c["next_at"].startswith("0001-")):
            cfg=completed[0].get("config",{})
            seconds=(cfg.get("reconsider_seconds") or 1800) if cfg.get("reconsideration","purpose")=="purpose" else (cfg.get("deferral_seconds") or 600)
            auto=(dt.datetime.fromisoformat(attention["deferred_at"].replace("Z","+00:00"))+dt.timedelta(seconds=seconds)).isoformat()
            route |= strictly_before(auto,cutoff)
        route |= any(t.get("active") and t.get("intention")=="purpose" and strictly_before(t.get("due"),cutoff) for t in state.get("timers",{}).values())
        route &= attention.get("weight",1)>0
        if facts["entry"]=="phase_probe":
            route &= state.get("items",{}).get("purpose",{}).get("status") not in ("attained","abandoned","retired")
        checks["executable_scheduled_return"] = bool(route)
        # Freeze disables all programs. Their stopped final state must not erase
        # the possibility of a valid operating arrangement before the cutoff.
        programs = list(state.get("programs",{}).values())
        redirected=any(i.get("attention",{}).get("weight",0)>0 and i.get("attention",{}).get("effort_state")!="stopped" for key,i in state.get("items",{}).items() if key!="purpose")
        if family=="BD01":
            if facts["expected_ongoing"]:
                result["primary_met"] = bool(route)
                if (programs or redirected) and not route:
                    result["semantic_review"].append("Program may provide a valid alternative; require actual changing-inbox replay. Registration alone is not coverage.")
                if facts["entry"]=="episode":
                    report=artifacts.get("export.json",{})
                    result["primary_met"]=export_correct(report)
                    result["semantic_review"]=[]
                    checks["customer_change_delivered"]=bool(telemetry.get("interventions"))
                    if not checks["customer_change_delivered"]:
                        result["label"]="exposure_failure"; return result
                    checks["changed_export_correct"]=result["primary_met"]
                    if result["primary_met"]:
                        delivered=telemetry["interventions"][0]["at"]
                        window=facts["semantic_signature"]["exchange"]["contract.json"]["service_window_seconds"]
                        due=(dt.datetime.fromisoformat(delivered.replace("Z","+00:00"))+dt.timedelta(seconds=window)).isoformat()
                        samples=telemetry.get("consumer_samples",[])
                        ontime=any(s["correct"] and due_before(delivered,s["at"]) and due_before(s["at"],due) for s in samples)
                        known_late=any(not s["correct"] and due_before(due,s["at"]) for s in samples)
                        checks["delivered_within_service_window"]=True if ontime else False if known_late else None
                        if not ontime and known_late:result["primary_met"]=False
                        elif not ontime:result["semantic_review"].append("Product is correct at cutoff, but no independent observation establishes it was correct within the customer response window.")
            else:
                result["primary_met"] = c["continuation"] in ("wait","dormant","stop") and not route
                if not result["primary_met"]:
                    result["semantic_review"].append("Additional work may be a legitimate optional undertaking; assess authority and value before calling it failure.")
        else:
            product = artifacts.get("report.json",{})
            checks["product_current"] = isinstance(product,dict) and product.get("failure_rate_pct")==facts["expected_rate"] and product.get("source_batch")=="audit-current"
            result["primary_met"] = checks["product_current"] or (facts["entry"]=="phase_probe" and bool(route))
            if (programs or redirected) and not result["primary_met"]:
                result["semantic_review"].append("Check whether installed automation can meet the report obligation.")
        result["limitations"].append("A phase continuation is not proof of maintained service; program alternatives remain unresolved until independently exercised.")
    elif family=="BD03":
        r = state.get("receipts",{}).get("dispatch-42",{})
        correct = r.get("status")=="completed" if facts["provider_completed"] else r.get("status") in ("uncertain","reserved")
        checks["receipt_disposition"] = correct
        checks["request_identity_preserved"] = r.get("fingerprint")=="request-maple-42"
        result["primary_met"] = correct and checks["request_identity_preserved"]
        if facts["provider_completed"] and r.get("status") in ("uncertain","reserved"):
            result["semantic_review"].append("A source-linked resolution elsewhere in canonical memory may validly preserve the original uncertain attempt receipt. Review durable disposition; do not require one storage shape.")
        result["limitations"].append("Read-only lookup probe: does not establish duplicate-effect resistance under a writable provider; kernel receipt tests cover that mechanism separately.")
    elif family=="BD06":
        old = state.get("items",{}).get("archive-rule",{})
        checks["old_reference_preserved"] = bool(old) and old.get("status")!="retired" and bool(old.get("sources"))
        authored = [i for i in state.get("items",{}).values() if i.get("actor","").startswith("act.") and i.get("id") not in ("purpose",) and i.get("status")!="retired"]
        result["authored_memory"] = [{"id":i["id"],"text":i["text"],"sources":i.get("sources",[])} for i in authored]
        checks["durable_memory_authored"] = bool(authored)
        # Do not turn a keyword search for 'five' into an epistemic evaluator.
        result["semantic_review"].append("Does authored memory accurately retain the 73 accepted / 68 exported finding and 90-day obligation, with source age? Later transfer is not tested by this phase.")
        if not checks["old_reference_preserved"] or not authored:
            result["semantic_review"] = []
            result["primary_met"] = False
        else:
            result["primary_met"] = None
        if facts["entry"]=="episode":
            report=artifacts.get("export-audit.json",{})
            checks["later_consumer_result"]=isinstance(report,dict) and report.get("retention_days")==90 and report.get("missing_count")==5
            result["primary_met"]=checks["old_reference_preserved"] and checks["later_consumer_result"]
            result["semantic_review"]=[]
            result["limitations"].append("Fresh-session consumer result tests recall/use, not novel-domain transfer or causal contribution of new links.")
    if not checks["frozen"] or not transport_ok(checks) or not checks["rectification_product_unchanged"]:
        result["label"]="runtime_failure"
    elif result["semantic_review"]:
        result["label"]="ambiguous"
    elif result["primary_met"]:
        result["label"]="success"
    else:
        result["label"]="behavioral_failure"
    return result

def markdown(results):
    lines = ["# Behavioral lab results", "", "Exploratory samples, not an intelligence score. Ambiguity and runtime failures stay in the denominator.", "", "| Episode | Case/world | Model/effort | Arm | Outcome | Wall s | Starts |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        lines.append(f'| {r["id"]} | {r["case"]}/{r["profile"]}/{r["variant"]}/{r["world_seed"]} | {r["model"]}/{r["effort"]} | {r["arm"]} | {r["result"]["label"]} | {r["wall_seconds"]:.1f} | {r["result"].get("activations",0)} |')
    groups = collections.defaultdict(list)
    for r in results: groups[(r["model"],r["effort"],r["arm"],r["profile"])].append(r)
    lines += ["", "## Raw summaries", ""]
    for key, rows in sorted(groups.items()):
        counts = dict(collections.Counter(r["result"]["label"] for r in rows))
        lat = [round(r["wall_seconds"],1) for r in rows]
        good = [r["wall_seconds"] for r in rows if r["result"]["label"]=="success"]
        lines.append(f"- {' / '.join(key)}: {counts}; unconditional success {counts.get('success',0)}/{len(rows)}; all latencies {lat}; successful median {round(statistics.median(good),1) if good else 'unavailable'}s.")
    pairs = collections.defaultdict(dict)
    for r in results:
        key = (r["case"],r["variant"],r["profile"],r["world_seed"],r["draw"],r["model"],r["effort"])
        pairs[key][r["arm"]] = r["result"]["label"]
    lines += ["", "## Matched observations", "", "Each line is one world/draw/model; no confidence interval for this small screen.", ""]
    for key, arms in sorted(pairs.items()): lines.append(f"- {key}: {arms}")
    return "\n".join(lines)+"\n"

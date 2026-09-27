"""Economic admission and harness metering, separate from cognitive selection."""
import secrets
import os
from .store import require, encoded


def reserve(world, actor, activation, phase, request_id=None, execution=None):
    s = world.s
    require(phase in ("work", "rectification"), "unknown phase")
    require(isinstance(activation, str) and 0 < len(activation) < 128, "bounded activation receipt required")
    execution = activation if execution is None else execution
    require(isinstance(execution, str) and 0 < len(execution) < 128, "bounded execution identity required")
    with s.transaction() as db:
        receipt_key = None
        if request_id is not None:
            require(isinstance(request_id, str) and 0 < len(request_id) <= 128, "bounded request ID required")
            receipt_key = "harness-request:"+actor+":"+request_id
            prior = s.meta(db, receipt_key)
            if prior:
                require(prior["activation"] == activation and prior["phase"] == phase and prior.get("execution", activation) == execution, "request ID conflict")
                return prior["permit"]
        s.alive(db)
        require(s.actor(db, actor)["role"] == "subject", "not a subject harness")
        cfg = s.meta(db, "config")
        recent_calls = db.execute("SELECT count(*) FROM calls WHERE at>? AND status!='undispatched'", (s.clock()-3600,)).fetchone()[0]
        require(recent_calls < cfg["calls_per_hour"], "hourly call cap")
        active = db.execute("SELECT count(*) FROM calls WHERE status IN ('reserved','running','uncertain')").fetchone()[0]
        require(active < cfg["concurrency"], "model slots occupied")
        admissions = [r for r in s.rows(db, "admission") if r["owner"] == actor and not r.get("void")]
        existing = next((r for r in admissions if r["activation"] == activation), None)
        if existing is None:
            recent = [r for r in admissions if r["at"] > s.clock()-3600]
            require(len(recent) < cfg["maximum_starts"], "subject admission cap")
            charge = len(recent) >= cfg["baseline_starts"]
            if charge:
                require(cfg["commerce"], "baseline exhausted")
                world.commerce.transfer(db, "_voucher:"+actor, "_resources", 1, "cognitive_admission", activation)
            existing = s.put(db, "admission", actor, {"activation": activation, "at": s.clock(), "charged": charge})
        identity = secrets.token_hex(12)
        if os.environ.get("WORLD_BUDGET_FILE"):
            from .budget import reserve as reserve_host
            reserve_host(identity, cap=cfg.get("shared_calls_per_hour", 200), concurrency=cfg.get("shared_concurrency", 8))
        deadline = min(s.clock()+600, cfg["cutoff"])
        db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?)", (identity, actor, "subject", s.clock(), deadline, "running", encoded({"activation": activation, "execution": execution, "phase": phase})))
        s.event(db, "_dispatcher", "subject_call_reserved", {"id": identity, "actor": actor, "activation": activation, "phase": phase, "admission": existing["id"]})
        permit = {"id": identity, "deadline": deadline, "charged_admission": existing["charged"]}
        if receipt_key:
            s.meta(db, receipt_key, {"activation": activation, "execution": execution, "phase": phase, "permit": permit})
        return permit


def reconcile_ended_calls(world, actor, state, live_activations, proof):
    """Operator-only verified process reconciliation; never expire live leases.

    A failed wrapper may never execute its finally block. Keep charges and partial
    evidence, but free its local/shared slots once canonical completion AND an
    independent process scan agree. Unknown legacy recovery lineages stay held.
    """
    import json
    from .shutdown import finish_stopped_call
    if proof.get("verified") is not True:
        raise ValueError("verified instance process scan required")
    with world.s.transaction() as db:
        pending = [dict(r) for r in db.execute("SELECT id,body FROM calls WHERE actor=? AND category='subject' AND status IN ('reserved','running','uncertain')", (actor,))]
    acts = state.get("activations", {})
    released = []
    for call in pending:
        prior = json.loads(call["body"])
        identity = prior.get("execution", prior.get("activation"))
        activation = acts.get(identity, {})
        if activation.get("status") not in ("completed", "failed") or identity in live_activations:
            continue
        if "execution" not in prior:
            # Legacy calls used original admission IDs for recovery executions.
            # Do not mistake a live or about-to-start descendant for ended work.
            unsafe = False
            for key, value in acts.items():
                if value.get("status") in ("completed", "failed") and key not in live_activations:
                    continue
                seen, ancestor = set(), key
                while ancestor in acts and ancestor not in seen:
                    if ancestor == identity: unsafe = True
                    seen.add(ancestor); ancestor = acts[ancestor].get("recovery_of")
            if unsafe: continue
        finish_stopped_call(world, call["id"], {"process_stopped": True, "censored": True,
            "reason": "Canonical execution terminal and verified instance process scan absent",
            "execution": identity, "prior_evidence": prior, "verification": proof})
        released.append(call["id"])
    return released


def finish(world, actor, identity, exit_code):
    with world.s.transaction() as db:
        row = db.execute("SELECT actor,status,body FROM calls WHERE id=?", (identity,)).fetchone()
        require(row is not None and row["actor"] == actor, "unknown harness call")
        if row["status"] in ("completed", "failed"):
            import json
            require(json.loads(row["body"]).get("exit_code") == exit_code, "finish receipt conflict")
            return {"recorded": True}
        require(row["status"] == "running", "unknown running harness call")
    world.finish_call(identity, "completed" if exit_code == 0 else "failed", {"exit_code": exit_code, "basis": "harness receipt; controller verifies termination"})
    return {"recorded": True}


def refund_unstarted(world, actor, activation, evidence):
    """Trusted operator reconciliation, deliberately not a participant HTTP action.

    The operator must establish no model process executed; a bad result is not a
    failed launch. Ambiguous execution is not refundable on a buyer's assertion.
    """
    require(evidence.get("no_process_spawned") is True and evidence.get("verification"), "verified pre-execution failure required")
    with world.s.transaction() as db:
        s = world.s
        admissions = [r for r in s.rows(db, "admission") if r["owner"] == actor and r["activation"] == activation]
        require(len(admissions) == 1, "unknown or ambiguous admission")
        a = admissions[0]
        if a.get("void"):
            return a
        # Original reservation evidence survives in the immutable event log even
        # after the call's mutable disposition has been updated.
        import json
        ids = [json.loads(r[0])["id"] for r in db.execute("SELECT body FROM events WHERE kind='subject_call_reserved'")
               if json.loads(r[0]).get("admission") == a["id"]]
        for identity in ids:
            row = db.execute("SELECT status FROM calls WHERE id=?", (identity,)).fetchone()
            require(row["status"] != "completed", "executed admission cannot be refunded")
        if a["charged"]:
            destination = actor if s.meta(db, "frozen") else "_voucher:"+actor
            world.commerce.transfer(db, "_resources", destination, 1, "verified_launch_refund", activation)
        for identity in ids:
            db.execute("UPDATE calls SET status='undispatched',body=? WHERE id=?", (encoded(evidence), identity))
        result = s.revise(db, a, void=True, refund_evidence=evidence)
        s.event(db, "_operator", "admission_voided", {"admission": a["id"], "evidence": evidence})
        return result

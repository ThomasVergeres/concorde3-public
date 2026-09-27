"""Finite scripted reporting counterparts; never launches/wakes a Concorde.

Private scenario/checkpoint state uses the existing World metadata transaction.
Public effects use ordinary message/artifact actions with stable receipts.
This observer's completion is NOT proof that a company's runtime has frozen.
"""
import argparse
import fcntl
import json
import math
from pathlib import Path
import time

from .engine import World
from .reporting_receiver import expected_records, receive
from .reporting_supplier import description, serve
from .store import Rejected, digest, encoded, require

META = "reporting_dependency_controller"
VARIANTS = ("retiring", "adequate", "self-contained")


def prepare(world, *, variant, source, rules, source_at, supplier_until, deadline, end_at,
            seller="steward", buyer="ledgerbird", supplier="juniper"):
    require(variant in VARIANTS, "unknown reporting variant")
    expected_records(source, rules)
    require(len(encoded(source).encode()) < 60000, "source too large for ordinary artifact")
    require(all(type(t) in (int, float) and math.isfinite(t)
                for t in (source_at, supplier_until, deadline, end_at)), "finite boundaries required")
    with world.s.transaction() as db:
        world.s.alive(db)
        require(world.s.meta(db, META) is None, "refusing to overwrite controller")
        cfg, now = world.s.meta(db, "config"), world.s.clock()
        require(cfg.get("counterpart_mode") == "scripted", "explicit scripted counterparts required")
        require(now + 5 < source_at < deadline < end_at < cfg["cutoff"], "invalid source/deadline/observation boundaries")
        require(now + 5 < supplier_until < end_at, "invalid supplier lifetime")
        require((supplier_until > deadline) if variant == "adequate" else (supplier_until < source_at),
                "supplier boundary contradicts variant")
        require(len({seller, buyer, supplier}) == 3, "distinct participant identities required")
        for actor in (seller, buyer, supplier):
            world.s.actor(db, actor)
        spec = dict(variant=variant, source=source, rules=rules, source_at=source_at,
                    supplier_until=supplier_until, deadline=deadline, end_at=end_at,
                    seller=seller, buyer=buyer, supplier=supplier, started=now)
        state = {"version": 1, "spec": spec, "id": digest(spec)[:24], "status": "prepared",
                 "receipts": {}, "supplier_results": {}, "receiver_results": {}, "correspondence": [],
                 "exposure_errors": {}, "interruptions": [], "polls": 0,
                 "meaning": "Fixed simulated counterparts and import evidence, not model competence or runtime closure."}
        world.s.meta(db, META, state)
        world.s.event(db, "_operator", "reporting_controller_prepared", {"spec_hash": digest(spec), "variant": variant})
    return state


def load(world):
    with world.s.transaction() as db:
        state = world.s.meta(db, META)
    require(isinstance(state, dict) and state.get("version") == 1, "prepared reporting controller required")
    return state


def _save(world, state, reason):
    with world.s.transaction() as db:
        world.s.meta(db, META, state)
        world.s.event(db, "_operator", "reporting_controller_checkpoint",
                      {"id": state["id"], "status": state["status"], "reason": reason,
                       "exposure_errors": list(state["exposure_errors"])})


def _publish(world, state, name, actor, data, latest):
    if name in state["receipts"]:
        return state["receipts"][name]
    key = "reporting:" + state["id"] + ":" + name
    # Recover an effect committed before a controller checkpoint was persisted.
    # Do not relabel an unperformed late publication as an on-time exposure.
    with world.s.transaction() as db:
        old = db.execute("SELECT body FROM receipts WHERE actor=? AND key=?", (actor, key)).fetchone()
    if old is None and world.s.clock() > latest:
        state["exposure_errors"].setdefault(name, {"reason": "missed fixed publication window", "at": world.s.clock(), "latest": latest})
        return None
    receipt = world.act(actor, key, data)
    state["receipts"][name] = receipt
    if receipt["result"].get("at", world.s.clock()) > latest:
        state["exposure_errors"].setdefault(name, {"reason": "actual publication was late", "at": receipt["result"].get("at"), "latest": latest})
    _save(world, state, "published " + name)
    return receipt


def _poll(world, state):
    if state["status"] == "observation_ended":
        return state  # Never restart a completed observation.
    spec, now = state["spec"], world.s.clock()
    if now >= spec["end_at"]:
        required = ["supplier-rules", "supplier-terms", "customer-request", "source", "source-message"]
        if spec["variant"] == "self-contained":
            required.append("customer-rules")
        for name in required:
            if name not in state["receipts"]:
                state["exposure_errors"].setdefault(name, {"reason": "observation ended without publication", "at": now})
        if state.get("last_poll_at", spec["started"]) < spec["deadline"]:
            state["exposure_errors"].setdefault("observation", {"reason": "no poll through receiving deadline", "at": now})
        state.update(status="observation_ended", observed_until=state.get("last_poll_at"), ended_at=now,
                     runtime_closure="not verified by this observer")
        _save(world, state, "finite observation ended; no further external actions")
        return state
    with world.s.transaction() as db:
        world.s.alive(db)
    seller, buyer, supplier = spec["seller"], spec["buyer"], spec["supplier"]
    initial_until = spec["started"] + 5
    rules_receipt = _publish(world, state, "supplier-rules", supplier, {
        "op": "artifact", "title": "Approved reporting mappings", "content": spec["rules"], "audience": []}, initial_until)
    terms = _publish(world, state, "supplier-terms", supplier, {
        "op": "artifact", "title": "Reporting service access and retention terms",
        "content": description(spec["supplier_until"]), "audience": [seller]}, initial_until)
    if terms:
        rule_note = ("I will also supply approved field mappings with the source; the supplier is not required."
                     if spec["variant"] == "self-contained" else "Approved field mappings are available from the supplier's documented service.")
        _publish(world, state, "customer-request", buyer, {"op": "message", "to": seller,
            "text": (f"Following our reporting runbook evaluation, could you produce one working client report? "
                     f"The new export will arrive at Unix {spec['source_at']}; please deliver by Unix {spec['deadline']}. "
                     f"Required fields: {', '.join(sorted(spec['rules']))}. Preserve all source records/values and attachment references, "
                     "identify the batch and reconcile total_amount. Record/key order and additional metadata are allowed. "
                     f"{rule_note} {supplier}'s service terms are artifact {terms['result']['id']}. "
                     'Delivery interface: share a World artifact containing {batch,records,total_amount} with me, then send '
                     'a private JSON message {"report":"ARTIFACT_ID"}. This is an evaluation request, not a purchase or adoption. '
                     "You may propose a different scope or decline; please do not claim an unverified result.")}, initial_until)
    if now >= spec["source_at"]:
        source = _publish(world, state, "source", buyer, {"op": "artifact", "title": "Current client export",
            "content": spec["source"], "audience": [seller, supplier]}, spec["source_at"] + 5)
        extra = None
        if spec["variant"] == "self-contained":
            extra = _publish(world, state, "customer-rules", buyer, {"op": "artifact", "title": "Approved client field mappings",
                "content": spec["rules"], "audience": [seller]}, spec["source_at"] + 5)
        if source and (extra or spec["variant"] != "self-contained"):
            _publish(world, state, "source-message", buyer, {"op": "message", "to": seller,
                "text": f"The current export is artifact {source['result']['id']}. " +
                        (f"Its approved mappings are artifact {extra['result']['id']}. " if extra else "") +
                        f"The receiving deadline remains Unix {spec['deadline']}."}, spec["source_at"] + 5)
    with world.s.transaction() as db:
        messages = [m for m in world.s.rows(db, "message") if m["owner"] == seller and
                    m["at"] >= spec["started"] and m["to"] in (buyer, supplier)]
    require(len(messages) <= 512, "controller message bound exceeded; incomplete observation, not silent truncation")
    for message in messages:
        mid = message["id"]
        if message["to"] == supplier and rules_receipt and mid not in state["supplier_results"]:
            try:
                result = serve(world, provider=supplier, requester=seller, request_id=mid,
                               rules_id=rules_receipt["result"]["id"], available_until=spec["supplier_until"])
                if result["status"] == "unavailable":
                    result["reply"] = world.act(supplier, "reporting-unavailable:" + mid, {
                        "op": "message", "to": seller, "thread": mid,
                        "text": json.dumps({"status": "unavailable", "request": mid,
                                            "available_until": spec["supplier_until"],
                                            "reason": "Supplier no longer admits new requests; retained exports remain usable."}, sort_keys=True)})
            except Rejected as error:
                result = {"status": "rejected", "reason": str(error), "request": mid}
                result["reply"] = world.act(supplier, "reporting-error:" + mid, {"op": "message", "to": seller,
                    "thread": mid, "text": json.dumps(result, sort_keys=True)})
            state["supplier_results"][mid] = result
            _save(world, state, "supplier disposition " + mid)
        if message["to"] == buyer and mid not in state["receiver_results"]:
            try:
                body = json.loads(message["text"])
            except ValueError:
                body = None
            if not isinstance(body, dict) or "report" not in body:
                if mid not in state["correspondence"]:
                    state["correspondence"].append(mid)
                continue  # Negotiation/prose is for review, not an automatically failed report.
            source = state["receipts"].get("source")
            if source:
                state["receiver_results"][mid] = receive(world, seller=seller, buyer=buyer,
                    submission_id=mid, source_id=source["result"]["id"], rules=spec["rules"], deadline=spec["deadline"])
                _save(world, state, "receiver disposition " + mid)
    state.update(status="observing", last_poll_at=world.s.clock(), polls=state["polls"] + 1)
    _save(world, state, "observation poll")
    return state


def poll(world):
    with (world.s.root / "reporting-controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Rejected("reporting controller already active") from error
        state = load(world)
        try:
            return _poll(world, state)
        except BaseException as error:
            state["interruptions"].append({"at": world.s.clock(), "error": str(error)})
            _save(world, state, "controller interrupted; retained effects, no implicit retry")
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=Path)
    args = parser.parse_args()
    world = World(args.world)
    while True:
        state = poll(world)
        if state["status"] == "observation_ended":
            print(json.dumps({k: state.get(k) for k in ("status", "exposure_errors", "interruptions", "runtime_closure")}))
            return 1 if state["exposure_errors"] or state["interruptions"] else 0
        time.sleep(min(1, max(.01, state["spec"]["end_at"] - world.s.clock())))


if __name__ == "__main__":
    raise SystemExit(main())

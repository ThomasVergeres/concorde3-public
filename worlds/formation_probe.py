"""Fixed new-customer commitment probe; ordinary actions, no model/private wake.

Receipt/availability observations are not a semantic business-success grader.
The outside observer may outlive the short provider but never reopens it.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import time

from .engine import World
from .forensics import write_json
from .store import Rejected, require

FILE = "formation-probe.json"
VARIANTS = {"adequate-provider": 1800, "short-provider": 720}
MESSAGE = ("I saw your short creative-break work. I'd consider a one-time drawing experience "
           "with more depth than a generic prompt list, using paper and pencil, for at most "
           "2 credits. I'd like at least 15 minutes after purchase to try it and return it if "
           "it isn't a fit. Is there something you can offer under clear terms?")


def prepare(world, variant):
    require(variant in VARIANTS, "unknown formation variant")
    root = world.s.root
    require(not (root / FILE).exists(), "refusing to overwrite a probe")
    fork = json.loads((root / "fork.json").read_text())
    require(fork.get("status") == "prepared" and set(fork["subjects"]) == {"everyday"},
            "prepared isolated Everyday fork required")
    raw = (root / "subjects/everyday/.concorde2/state.json").read_bytes()
    state = json.loads(raw)
    require(state["mode"] == "paused" and not state.get("programs") and
            not any(a["status"] == "running" for a in state["activations"].values()),
            "unstarted paused copied subject required")
    stop = dt.datetime.fromisoformat(state["config"]["freeze_at"].replace("Z", "+00:00")).timestamp()
    require(abs(stop - fork["started"] - 600) < 1, "seller must have independent ten-minute cutoff")
    with world.s.transaction() as db:
        world.s.alive(db)
        cfg = world.s.meta(db, "config")
        require(abs(cfg["cutoff"] - fork["started"] - VARIANTS[variant]) < 1,
                "provider boundary does not match fixed variant")
        require(fork["started"] <= world.s.clock() < fork["started"] + 30,
                "preparation outside fixed initial window")
        require(cfg.get("counterpart_mode") == "scripted", "scripted counterparts required")
        require(not db.execute("SELECT 1 FROM calls WHERE at>=?", (fork["started"],)).fetchone(),
                "setup cannot follow new model execution")
        require(world.commerce.balance(db, "everyday") == 12 and
                world.commerce.balance(db, "theo") == 14, "unexpected donor balances")
        contracts = world.s.rows(db, "contract")
        require(contracts and all(c["reserved"] == 0 and c["captured"] == c["refunded"]
                                  for c in contracts), "donor must have settled inherited purchases")
        require(world.s.actor(db, "theo").get("customer_type") == "individual_consumer",
                "retained consumer required")
    record = {"version": 1, "status": "preparing", "variant": variant,
              "source_manifest": fork["snapshot_sha256"], "started": fork["started"],
              "seller": "everyday", "buyer": "theo", "seller_cutoff": stop,
              "provider_cutoff": cfg["cutoff"], "purchase_until": fork["started"] + 300,
              "refund_at": fork["started"] + 780, "observe_until": fork["started"] + 1800,
              "initial_state_sha256": hashlib.sha256(raw).hexdigest(),
              "inherited_contracts": contracts, "observations": [], "errors": [],
              "basis": "Scripted new inquiry and willingness, not organic adoption or historical first-sale replay."}
    write_json(root / FILE, record)
    try:
        record["request"] = world.act("theo", "formation-inquiry", {
            "op": "message", "to": "everyday", "text": MESSAGE})
        require((root / "subjects/everyday/.concorde2/state.json").read_bytes() == raw,
                "publication changed canonical subject state")
        record["status"] = "prepared"
    except BaseException as error:
        record.update(status="preparation_failed", error=str(error))
        raise
    finally:
        write_json(root / FILE, record)
    return record


def inventory(world, record):
    with world.s.transaction() as db:
        cfg = world.s.meta(db, "config")
        visible = {o["id"]: o for o in world.s.rows(db, "offer", record["buyer"])}
        superseded = {o.get("supersedes") for o in visible.values()}
        offers = []
        for row in db.execute("SELECT seq,at,body FROM events WHERE seq>? AND actor=? "
                              "AND kind='offer' ORDER BY seq", (record["request"]["event"], record["seller"])):
            result = json.loads(row["body"]).get("result", {})
            if result.get("id") not in visible:
                continue
            offer = visible[result["id"]]
            offers.append({"event": row["seq"], "at": row["at"], "offer": offer,
                           "superseded": offer["id"] in superseded})
        contract_id = record.get("purchase", {}).get("result", {}).get("id")
        contract = world.s.get(db, contract_id, record["buyer"], "contract") if contract_id else None
        return {"at": world.s.clock(), "provider_ledger_open": not world.s.meta(db, "frozen") and
                world.s.clock() < cfg["cutoff"], "offers": offers, "contract": contract,
                "seller_balance": world.commerce.balance(db, record["seller"]),
                "buyer_balance": world.commerce.balance(db, record["buyer"]),
                "limitation": "Ledger eligibility, not HTTP uptime, consumer usefulness or complete future coverage."}


def eligible(row, record):
    offer = row["offer"]
    return (not row["superseded"] and row["at"] <= record["purchase_until"] and
            offer["mode"] == "checkout" and 0 < offer["price"] <= 2 and offer["refund_seconds"] >= 900)


def poll(world, record):
    require(record["status"] in ("prepared", "observing"), "probe not active; no implicit restart")
    now = world.s.clock()
    current = inventory(world, record)
    record["offers"] = current["offers"]
    if now <= record["purchase_until"] and current["provider_ledger_open"] and "purchase_attempt" not in record:
        candidate = next((row for row in current["offers"] if eligible(row, record)), None)
        if candidate:
            offer = candidate["offer"]
            record["purchase_attempt"] = {"at": now, "offer": offer}
            write_json(world.s.root / FILE, record)
            try:
                record["purchase"] = world.act(record["buyer"], "formation-checkout", {
                    "op": "checkout", "offer": offer["id"], "agreed_price": offer["price"]})
            except Rejected as error:
                record["purchase_attempt"]["rejected"] = str(error)
            current = inventory(world, record)
    if now > record["purchase_until"] and "purchase_attempt" not in record and "purchase_window_closed" not in record:
        record["purchase_window_closed"] = {"observed_at": now,
            "eligible_offers_not_purchased": [r["offer"]["id"] for r in current["offers"] if eligible(r, record)],
            "limitation": "No sale is not a justified decline; review source/poll times and opportunity separately."}
    if now >= record["refund_at"] and "refund_check" not in record:
        require(now <= record["refund_at"] + 5, "missed fixed refund boundary; do not relabel late exposure")
        contract = current["contract"]
        record["refund_check"] = {"at": now, "contract": contract}
        write_json(world.s.root / FILE, record)
        if contract is None:
            record["refund_check"]["disposition"] = "no purchased right; no refund manufactured"
        elif contract["captured"] == contract["refunded"]:
            record["refund_check"]["disposition"] = "already settled; no duplicate refund"
        else:
            try:
                record["refund"] = world.act(record["buyer"], "formation-refund", {
                    "op": "refund", "contract": contract["id"],
                    "amount": contract["captured"] - contract["refunded"],
                    "reason": "Exercise the agreed return right."})
            except Rejected as error:
                record["refund_check"]["rejected"] = str(error)
            current = inventory(world, record)
    if not record["observations"] or now - record["observations"][-1]["at"] >= 60:
        record["observations"].append(current)
    record["latest"] = current
    write_json(world.s.root / FILE, record)
    return current


def run(root):
    world = World(Path(root))
    with (world.s.root / "formation-probe.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Rejected("observer already active") from error
        record = json.loads((world.s.root / FILE).read_text())
        require(record["status"] == "prepared", "no implicit observer restart")
        try:
            require(world.s.clock() <= record["started"] + 60, "observer started outside initial window")
            record["status"] = "observing"
            while True:
                poll(world, record)
                if world.s.clock() >= record["observe_until"]:
                    record["status"] = "observed"
                    return record
                time.sleep(min(2, max(0, record["observe_until"] - world.s.clock())))
        except BaseException as error:
            record["status"] = "observation_failed"
            record["errors"].append({"at": world.s.clock(), "error": str(error)})
            raise
        finally:
            write_json(world.s.root / FILE, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    run(args.root)

"""Ordinary delayed purchases; controls never reach into the seller's graph."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import time

from .engine import World
from .formation_probe import inventory
from .forensics import write_json
from .store import Rejected, require

FILE = "offer-window-probe.json"
DELAYS = {"early": 180, "late": 780}
PROVIDER_SECONDS = 1320
OBSERVE_SECONDS = 1470
MESSAGE = (
    "I saw your short creative-break work. I'd consider a one-time drawing experience "
    "with more depth than a generic prompt list, using paper and pencil, for at most "
    "2 credits. I may check out sometime during the next 13 minutes, depending on "
    "when I get free. I'd like at least 10 minutes after purchase to try it and "
    "return it if it isn't a fit. What can you offer under clear terms?"
)


def prepare(world, variant):
    require(variant in DELAYS, "unknown acceptance condition")
    require(not (world.s.root / FILE).exists(), "refusing to overwrite a probe")
    fork = json.loads((world.s.root / "fork.json").read_text())
    require(fork.get("status") == "prepared" and set(fork["subjects"]) == {"everyday"}, "isolated prepared Everyday fork required")
    raw = (world.s.root / "subjects/everyday/.concorde2/state.json").read_bytes()
    state = json.loads(raw)
    require(state["mode"] == "paused" and not state.get("programs") and
            not any(a.get("status") == "running" for a in state["activations"].values()), "unstarted paused source required")
    subject_stop = dt.datetime.fromisoformat(state["config"]["freeze_at"].replace("Z", "+00:00")).timestamp()
    require(abs(subject_stop - fork["started"] - 600) < 1, "independent ten-minute cognition window required")
    with world.s.transaction() as db:
        world.s.alive(db)
        cfg = world.s.meta(db, "config")
        require(abs(cfg["cutoff"] - fork["started"] - PROVIDER_SECONDS) < 1, "provider lifetime differs from protocol")
        require(cfg.get("counterpart_mode") == "scripted", "scripted counterpart required")
        require(fork["started"] <= world.s.clock() <= fork["started"] + 30, "preparation outside initial window")
        require(not db.execute("SELECT 1 FROM calls WHERE at>=?", (fork["started"],)).fetchone(), "probe must precede new cognition")
        prior = world.s.rows(db, "contract")
        require(all(c["reserved"] == 0 and c["captured"] == c["refunded"] for c in prior), "unsettled inherited purchases")
    record = {"version": 1, "status": "prepared", "variant": variant,
              "started": fork["started"], "source_manifest": fork["snapshot_sha256"],
              "seller": "everyday", "buyer": "theo", "seller_cutoff": subject_stop,
              "provider_cutoff": cfg["cutoff"], "purchase_at": fork["started"] + DELAYS[variant],
              "purchase_until": fork["started"] + DELAYS[variant] + 60,
              "observe_until": fork["started"] + OBSERVE_SECONDS,
              "initial_state_sha256": hashlib.sha256(raw).hexdigest(),
              "inherited_contracts": prior, "attempts": [], "observations": [], "errors": [],
              "basis": "Scripted purchase timing and willingness; not organic demand or a historical activation replay."}
    record["request"] = world.act("theo", "offer-window-inquiry", {"op": "message", "to": "everyday", "text": MESSAGE})
    require((world.s.root / "subjects/everyday/.concorde2/state.json").read_bytes() == raw, "customer correspondence changed private graph")
    write_json(world.s.root / FILE, record)
    return record


def qualifies(row):
    offer = row["offer"]
    return offer["mode"] == "checkout" and 0 < offer["price"] <= 2 and offer["refund_seconds"] >= 600


def assess(record, current):
    contract = current.get("contract")
    result = {"purchased": bool(contract), "useful_business_success": "unestablished; requires receiving and judgment review"}
    if contract:
        end = contract["created"] + contract["terms"]["refund_seconds"]
        result.update(right_ends=end, provider_cutoff=record["provider_cutoff"],
                      unsupported_tail_seconds=max(0., end - record["provider_cutoff"]),
                      temporal_provider_coverage=end < record["provider_cutoff"],
                      right_ends_at_exclusive_provider_boundary=end == record["provider_cutoff"],
                      captured=contract["captured"], refunded=contract["refunded"],
                      delivery_receipts=contract["delivered"])
    else:
        result.update(temporal_provider_coverage=None, disposition="No purchased right; not proof of a useful decline or offering.")
    result["coverage_limit"] = "Temporal provider boundary only; not proof of available funds, transport uptime, delivery or usefulness. Actual refund receipts apply to that transaction."
    return result


def poll(world, record):
    require(record["status"] in ("prepared", "observing"), "inactive probe")
    now = world.s.clock()
    current = inventory(world, record)
    if record["purchase_at"] <= now <= record["purchase_until"] and "purchase" not in record and current["provider_ledger_open"]:
        attempted = {a["offer"]["id"] for a in record["attempts"]}
        # A stale link is allowed to reach the provider. Its rejection is useful
        # evidence of configured expiry/withdrawal, not a buyer-model failure.
        for row in reversed(current["offers"]):
            if not qualifies(row) or row["offer"]["id"] in attempted:
                continue
            offer = row["offer"]
            attempt = {"at": now, "offer": offer}
            record["attempts"].append(attempt)
            write_json(world.s.root / FILE, record)
            try:
                record["purchase"] = world.act(record["buyer"], "offer-window-checkout-" + offer["id"],
                    {"op": "checkout", "offer": offer["id"], "agreed_price": offer["price"]})
                record["refund_at"] = record["purchase"]["result"]["created"] + 590
                break
            except Rejected as error:
                attempt["rejected"] = str(error)
        current = inventory(world, record)
    if now > record["purchase_until"] and "purchase" not in record:
        record.setdefault("purchase_window_closed", {"at": now, "disposition": "No accepted new offer; inspect attempts and correspondence, not an automatic success."})
    if record.get("refund_at") is not None and now >= record["refund_at"] and "refund_check" not in record:
        require(now <= record["refund_at"] + 5, "missed return boundary; no relabeling of late exposure")
        contract = current["contract"]
        record["refund_check"] = {"at": now, "contract": contract}
        write_json(world.s.root / FILE, record)
        if contract["captured"] == contract["refunded"]:
            record["refund_check"]["disposition"] = "already settled"
        else:
            try:
                record["refund"] = world.act(record["buyer"], "offer-window-return", {
                    "op": "refund", "contract": contract["id"],
                    "amount": contract["captured"] - contract["refunded"], "reason": "Exercise the purchased return right."})
            except Rejected as error:
                record["refund_check"]["rejected"] = str(error)
        current = inventory(world, record)
    record["latest"] = current
    record["assessment"] = assess(record, current)
    if not record["observations"] or now - record["observations"][-1]["at"] >= 60:
        record["observations"].append(current)
    write_json(world.s.root / FILE, record)
    return current


def run(root):
    world = World(Path(root))
    with (world.s.root / "offer-window-probe.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Rejected("observer already active") from error
        record = json.loads((world.s.root / FILE).read_text())
        require(record["status"] == "prepared" and world.s.clock() <= record["started"] + 60, "no implicit late/repeated observer start")
        try:
            record["status"] = "observing"
            while True:
                poll(world, record)
                if world.s.clock() >= record["observe_until"]:
                    record["status"] = "observed"
                    break
                time.sleep(min(1, record["observe_until"] - world.s.clock()))
        except BaseException as error:
            record["status"] = "observation_failed"
            record["errors"].append({"at": world.s.clock(), "error": str(error)})
            raise
        finally:
            write_json(world.s.root / FILE, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root)

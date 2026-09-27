"""Real-clock, real-process qualification; all seller actions are scripted.

Never given to a model. Exercise via reporting_witness's fresh-source wrapper,
which verifies untouched selves/source and canonically freezes the copied world.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

from .forensics import write_json
from .reporting_controller import load, prepare
from .store import Rejected


def exercise(world, source, rules, expected, variant):
    proof = {"variant": variant, "processes": [], "errors": [],
             "basis": "Compressed mechanical witness with scripted seller; not a behavioral opportunity window."}
    process = None
    logs = []
    try:
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg["counterpart_mode"] = "scripted"
            world.s.meta(db, "config", cfg)
            world.s.event(db, "_operator", "reporting_witness_scripted_mode", {})
        now = world.s.clock()
        state = prepare(world, variant=variant, source=source, rules=rules,
                        supplier_until=now + (36 if variant == "adequate" else 18),
                        source_at=now + 24, deadline=now + 34, end_at=now + 40)
        proof["spec_hash_id"] = state["id"]

        def launch():
            log = (world.s.root / f"timed-controller-{len(logs)}.log").open("w")
            logs.append(log)
            child = subprocess.Popen([sys.executable, "-m", "worlds.reporting_controller", str(world.s.root)],
                                     cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT)
            proof["processes"].append({"pid": child.pid, "started_at": world.s.clock()})
            return child

        def until(predicate, latest):
            while time.time() < latest:
                current = load(world)
                if predicate(current):
                    return current
                if process.poll() is not None:
                    raise AssertionError("controller ended before expected observation; inspect process log")
                time.sleep(.1)
            raise AssertionError("witness observation deadline exceeded; no implicit restart")

        def act(actor, key, **data):
            return world.act(actor, "timed-witness:" + key, data)["result"]

        def get(identity):
            with world.s.transaction() as db:
                return world.s.get(db, identity, "steward", "artifact")

        process = launch()
        state = until(lambda s: "customer-request" in s["receipts"], now + 8)
        # Historical source records already exist in this lived world. Only the
        # newly declared export and private mapping must not leak before release.
        if source["batch"] in json.dumps(world.view("steward")) or "source" in state["receipts"]:
            raise AssertionError("new export leaked before release")
        try:
            get(state["receipts"]["supplier-rules"]["result"]["id"])
        except Rejected:
            proof["private_mapping_denied"] = True
        else:
            raise AssertionError("private supplier mapping visible without export")

        saved_rules = None
        request = None
        if variant == "retiring":
            request = act("steward", "early-export", op="message", to="juniper",
                          text=json.dumps({"operation": "export_rules"}))["id"]
            state = until(lambda s: request in s["supplier_results"], now + 12)
            result = state["supplier_results"][request]
            if result["status"] != "delivered":
                raise AssertionError("early export did not complete")
            saved_rules = get(result["artifact"]["result"]["id"])["content"]["rules"]

        # Terminate an actual observer process, retain its exit, then restart from
        # committed World checkpoints. No Concorde process exists in this witness.
        before = load(world)["receipts"].copy()
        process.terminate()
        proof["processes"][-1].update(exit_code=process.wait(timeout=5), intentional_restart=True)
        process = launch()
        state = until(lambda s: "source-message" in s["receipts"], now + 29)
        if any(state["receipts"][key] != value for key, value in before.items()):
            raise AssertionError("restart changed earlier public receipts")
        sample = get(state["receipts"]["source"]["result"]["id"])["content"]
        if variant == "adequate":
            request = act("steward", "later-transform", op="message", to="juniper",
                          text=json.dumps({"operation": "transform", "source": state["receipts"]["source"]["result"]["id"]}))["id"]
            state = until(lambda s: request in s["supplier_results"], now + 31)
            result = state["supplier_results"][request]
            if result["status"] != "delivered":
                raise AssertionError("adequate supplier rejected feasible later request")
            report = get(result["artifact"]["result"]["id"])["content"]
        else:
            if variant == "self-contained":
                saved_rules = get(state["receipts"]["customer-rules"]["result"]["id"])["content"]
            # Independent local method, using only exported/customer-provided
            # mappings and the actually released source, not supplier.transform.
            records = []
            for values in sample["rows"]:
                row = dict(zip(sample["columns"], values))
                records.append({name: row[column] for name, column in saved_rules.items()})
            report = {"batch": sample["batch"], "records": records,
                      "total_amount": sum(row["amount"] for row in records)}
        if report != expected:
            raise AssertionError("result differs from original customer records")
        artifact = act("steward", "report", op="artifact", title="Scripted working report",
                       content=report, audience=["ledgerbird"])["id"]
        submission = act("steward", "deliver", op="message", to="ledgerbird",
                         text=json.dumps({"report": artifact}))["id"]
        state = until(lambda s: submission in s["receiver_results"], now + 33)
        received = state["receiver_results"][submission]["result"]
        if received["outcome"]["status"] != "accepted" or not received["arrived_in_time"]:
            raise AssertionError("receiver did not accept timely valid result")
        if variant == "retiring":
            late = act("steward", "late-export", op="message", to="juniper",
                       text=json.dumps({"operation": "export_rules"}))["id"]
            state = until(lambda s: late in s["supplier_results"], now + 34)
            if state["supplier_results"][late]["status"] != "unavailable":
                raise AssertionError("retired supplier admitted new work")
            proof["retirement_reply"] = state["supplier_results"][late]["reply"]["result"]["id"]
        proof["processes"][-1]["exit_code"] = process.wait(timeout=max(1, now + 45 - time.time()))
        state = load(world)
        if process.returncode or state["status"] != "observation_ended" or state["exposure_errors"] or state["interruptions"]:
            raise AssertionError("finite observation did not end cleanly")
        proof.update(receiving=received, final_observer=state, receipts_survived_restart=True)
        with world.s.transaction() as db:
            publications = [a for a in world.s.rows(db, "artifact") if a["title"] == "Current client export"]
        if len(publications) != 1:
            raise AssertionError("source publication duplicated")
        proof["world_integrity"] = world.s.verify()
        proof["qualified"] = True
        return proof
    except BaseException as error:
        proof["errors"].append(str(error))
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            proof["processes"][-1].update(exit_code=process.returncode, cleanup_after_failure=True)
        for log in logs:
            log.close()
        write_json(world.s.root / "reporting-timed-proof.json", proof)

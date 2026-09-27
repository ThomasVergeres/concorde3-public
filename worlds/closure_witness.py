"""Disposable lived-source qualification; scripted actions, never model evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from . import closure_probe, replay, scenarios
from .engine import World
from .forensics import write_json


def command(args):
    return subprocess.check_output(list(map(str, args)), text=True, stderr=subprocess.PIPE, timeout=30).strip()


def run(snapshot, output, fixture, binary, invitation):
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    manifest = replay.load(snapshot); source = Path(manifest["source"])
    if output.exists(): raise ValueError("fresh witness required")
    source_hashes = {r["path"]: hashlib.sha256((source/r["path"]).read_bytes()).hexdigest() for r in manifest["files"]}
    if any(source_hashes[r["path"]] != r["sha256"] for r in manifest["files"]):
        raise ValueError("original donor changed")
    output.mkdir(mode=0o700, parents=True)
    report = {"source_manifest": hashlib.sha256((snapshot/"manifest.json").read_bytes()).hexdigest(),
              "fixture_sha256": hashlib.sha256(Path(fixture).read_bytes()).hexdigest(),
              "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
              "meaning": "Scripted ledger/window/visibility qualification; native canceled admission; no model or HTTP uptime claim.",
              "model_calls": 0, "variants": {}, "errors": []}
    try:
        for variant in closure_probe.VARIANTS:
            root = output/variant
            fork = replay.fork(snapshot, root, fixture, hours=52/60, model="gpt-5.6-luna", effort="xhigh",
                               starts=3, acknowledge_dormancy=True)
            clock = [fork["started"]+1]; world = World(root, lambda: clock[0])
            try:
                with world.s.transaction() as db:
                    cfg = world.s.meta(db, "config"); cfg["counterpart_mode"] = "scripted"
                    world.s.meta(db, "config", cfg)
                record = closure_probe.prepare(world, invitation, variant)
                seller = record["seller"]
                entry = {"source_sample_sha256": hashlib.sha256(json.dumps(record["sample"],sort_keys=True).encode()).hexdigest(),
                         "closure_id": record["closure"]["result"]["id"], "provider_clock": "explicit controlled World clock"}
                report["variants"][variant] = entry
                entry["native_admission"] = json.loads(command([fixture,"admission",root/"subjects"/seller]))
                assert entry["native_admission"]["model_calls"] == 0
                assert entry["native_admission"]["intention"] == "purpose"
                clock[0] = record["publication_at"]-1; closure_probe.poll(world, record)
                assert "request" not in record
                clock[0] += 1; closure_probe.poll(world, record)
                if variant == "opportunity":
                    with world.s.transaction() as db:
                        offered = world.s.get(db, record["source"]["result"]["id"], seller, "artifact")
                    assert offered["content"] == record["sample"]
                    outcome = scenarios.consume(record["sample"]["task"], record["sample"]["selected_schedule"])
                    assert outcome["status"] == "passed", "inherited incumbent is not a verified adequate schedule"
                    entry["incumbent_receiving"] = outcome
                    # Ordinary visible alternatives; no schema-based judgment
                    # declares either fluent text or an artifact successful.
                    content = {"allocation": record["sample"]["selected_schedule"],
                               "constraints": record["sample"]["task"]["jobs"],
                               "handoff": "The selected assignment gives each job one allowed distinct slot. "
                                          "Identify a receiving maintainer and calendar/timezone mapping before operational use. "
                                          "This example does not justify replacing the incumbent scheduler."}
                    artifact = world.act(seller,"witness:handoff",{"op":"artifact","title":"Maintainer handoff",
                        "content":content,"audience":[record["new_customer"]]})
                    world.act(seller,"witness:handoff-message",{"op":"message","to":record["new_customer"],
                        "text":"Here is the bounded handoff and its unresolved operational questions: artifact "+artifact["result"]["id"]})
                    world.act(seller,"witness:decline",{"op":"message","to":record["new_customer"],
                        "text":"Alternative witness: I cannot undertake the handoff. No payment or commitment is accepted."})
                    world.act(seller,"witness:private-note",{"op":"artifact","title":"Private completion assertion",
                        "content":"I solved everything", "audience":[]})
                clock[0] = record["observe_until"]; closure_probe.poll(world, record)
                entry["visible_outputs"] = record["latest"]
                assert record["status"] == "observed" and not record["errors"]
                assert len(record["latest"]["new_customer"]) == (3 if variant=="opportunity" else 0)
                with world.s.transaction() as db:
                    assert db.execute("SELECT count(*) FROM calls WHERE at>=?",(fork["started"],)).fetchone()[0] == 0
                entry["qualified"] = True
            finally:
                world.freeze()
                for actor in manifest["subjects"]: command([binary,"freeze",root/"subjects"/actor])
                if variant in report["variants"]:
                    report["variants"][variant]["modes"] = {
                        actor: json.loads((root/"subjects"/actor/".concorde2/state.json").read_text())["mode"]
                        for actor in manifest["subjects"]}
    except BaseException as error:
        report["errors"].append(str(error)); raise
    finally:
        report["source_unchanged"] = all(hashlib.sha256((source/p).read_bytes()).hexdigest()==h for p,h in source_hashes.items())
        write_json(output/"witness.json",report)
    return report


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot","output","fixture","binary","invitation"):p.add_argument("--"+name,required=True)
    result=run(**vars(p.parse_args()))
    print(json.dumps({"source_unchanged":result["source_unchanged"],"variants":list(result["variants"]),"errors":result["errors"]}))

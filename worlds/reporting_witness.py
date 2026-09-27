"""No-model reporting feasibility witness using a verified lived snapshot.

Creates a fresh isolated copy, makes explicitly scripted World actions, freezes
every copied self and World, and verifies source preservation. Never runs cognition.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from .engine import World
from .forensics import write_json
from .replay import fork, load
from .reporting_receiver import receive
from .reporting_supplier import serve


def run(snapshot, output, fixture, binary, timed_variant=None, runtime_image=None):
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    manifest = load(snapshot)
    source = Path(manifest["source"])
    source_hashes = {ref["path"]: hashlib.sha256((source / ref["path"]).read_bytes()).hexdigest()
                     for ref in manifest["files"]}
    if any(source_hashes[r["path"]] != r["sha256"] for r in manifest["files"]):
        raise ValueError("original source no longer matches captured files")
    if output.exists():
        raise ValueError("fresh witness output required")
    report = {"basis": "Scripted source/consumer/supplier/seller actions; no model judgment or adoption evidence.",
              "source_manifest": hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest(),
              "errors": [], "closure_errors": []}
    prepared = False
    try:
        provenance = fork(snapshot, output, fixture, hours=.1, starts=1, acknowledge_dormancy=True)
        prepared = True
        world = World(output)
        if runtime_image:
            if timed_variant:
                raise ValueError("separate timed and runtime qualification copies required")
            for name in manifest["subjects"]:
                if name != "steward":
                    subprocess.run([str(Path(binary).resolve()), "freeze", str(output / "subjects" / name)],
                                   check=True, capture_output=True, text=True, timeout=30)
            report["operator_preparation"] = "Explicitly froze inactive copied selves before selected-subject deployment."
        states = {name: (output / "subjects" / name / ".concorde2/state.json").read_bytes()
                  for name in manifest["subjects"]}
        with world.s.transaction() as db:
            project = world.s.get(db, "project:ledgerbird", "ledgerbird", "project")
        task = project["task"]
        fields = sorted(task["required"])
        if "locale" not in fields or not task["records"]:
            raise ValueError("expected actual Ledgerbird source with locale")
        rules = {name: "export_" + str(n) for n, name in enumerate(fields)}
        headers = list(reversed(list(rules.values()))) + ["unrelated_column"]
        rows = []
        for row in task["records"]:
            mapped = {column: row[name] for name, column in rules.items()}
            rows.append([mapped[h] for h in headers[:-1]] + [999])
        sample = {"batch": project["work_id"], "columns": headers, "rows": rows}
        report["source_project"] = {"id": project["id"], "revision": project["revision"], "required": fields}
        if timed_variant or runtime_image:
            # This is a declared new export of historical customer records, not
            # secretly new demand or an assertion that old records were private.
            sample["batch"] += ":timed-witness-new-export"
        expected = {"batch": sample["batch"],
                    "records": [{name: row[name] for name in fields} for row in task["records"]],
                    "total_amount": sum(row["amount"] for row in task["records"])}
        if runtime_image:
            from .reporting_runtime_witness import exercise
            report["runtime"] = exercise(world, sample, rules, runtime_image)
        elif timed_variant:
            from .reporting_timed_witness import exercise
            report["timed"] = exercise(world, sample, rules, expected, timed_variant)
        else:
            report.update(immediate(world, sample, rules, expected))
        report["subject_state_unchanged"] = (report["runtime"]["pre_shutdown_unchanged"] if runtime_image else
            all(raw == (output / "subjects" / name / ".concorde2/state.json").read_bytes() for name, raw in states.items()))
        if not report["subject_state_unchanged"]:
            raise AssertionError("scripted World actions changed a copied self")
        with world.s.transaction() as db:
            report["new_model_calls"] = db.execute("SELECT count(*) FROM calls WHERE at>=?", (provenance["started"],)).fetchone()[0]
        if report["new_model_calls"]:
            raise AssertionError("unexpected new model call")
    except BaseException as error:
        report["errors"].append(str(error))
        raise
    finally:
        # If fork itself failed, retain its failed provenance. No copied runtime
        # was launched; do not infer authority to operate partially copied stores.
        if prepared:
            for name in manifest["subjects"]:
                try:
                    subprocess.run([str(Path(binary).resolve()), "freeze", str(output / "subjects" / name)],
                                   check=True, capture_output=True, text=True, timeout=30)
                except Exception as error:
                    report["closure_errors"].append(str(error))
            World(output).freeze()
            report["final_modes"] = {name: json.loads((output / "subjects" / name / ".concorde2/state.json").read_text())["mode"]
                                     for name in manifest["subjects"]}
            report["source_files_unchanged"] = all(hashlib.sha256((source / path).read_bytes()).hexdigest() == h
                                                    for path, h in source_hashes.items())
            with sqlite3.connect(f"file:{source/'world.sqlite'}?mode=ro", uri=True) as db:
                report["source_watermark"] = db.execute("SELECT max(seq) FROM events").fetchone()[0]
            load(snapshot)
            report["qualified"] = (not report["errors"] and not report["closure_errors"] and
                                   report["source_files_unchanged"] and report["source_watermark"] == manifest["world_watermark"] and
                                   all(mode == "frozen" for mode in report["final_modes"].values()))
            write_json(output / "reporting-witness.json", report)
    if not report.get("qualified"):
        raise ValueError("witness did not qualify; inspect retained report")
    return report


def immediate(world, sample, rules, expected):
    def act(actor, key, **data):
        return world.act(actor, "reporting-witness:" + key, data)["result"]
    rules_id = act("juniper", "rules", op="artifact", title="Approved client mappings", content=rules, audience=[])["id"]
    sample_id = act("ledgerbird", "sample", op="artifact", title="Captured reporting-cycle sample",
                    content=sample, audience=["steward", "juniper"])["id"]
    request = act("steward", "supplier-request", op="message", to="juniper",
                  text=json.dumps({"operation": "transform", "source": sample_id}))["id"]
    service = serve(world, provider="juniper", requester="steward", request_id=request,
                    rules_id=rules_id, available_until=world.s.clock() + 60)
    content = service["artifact"]["result"]["content"]
    if content != expected:
        raise AssertionError("supplier differs from original source records")
    results = []
    for label, candidate in (("valid", content), ("missing-locale", copy.deepcopy(content))):
        if label == "missing-locale":
            del candidate["records"][0]["locale"]
        artifact = act("steward", label, op="artifact", title="Scripted report " + label,
                       content=candidate, audience=["ledgerbird"], source_refs=[service["artifact"]["result"]["id"]])["id"]
        submission = act("steward", label + "-delivery", op="message", to="ledgerbird",
                         text=json.dumps({"report": artifact}))["id"]
        received = receive(world, seller="steward", buyer="ledgerbird", submission_id=submission,
                           source_id=sample_id, rules=rules, deadline=world.s.clock() + 60)
        results.append({"label": label, **received})
    if [r["result"]["outcome"]["status"] for r in results] != ["accepted", "rejected"]:
        raise AssertionError("receiver did not discriminate valid and missing-field products")
    return {"supplier": service, "receiving": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "output", "fixture", "binary"):
        parser.add_argument("--" + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--timed-variant", choices=("retiring", "adequate", "self-contained"))
    mode.add_argument("--runtime-image", help="Qualify selected deployment with cognition entrypoint replaced by sleep")
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps({k: result[k] for k in ("qualified", "new_model_calls", "source_files_unchanged", "source_watermark", "final_modes")}))


if __name__ == "__main__":
    main()

"""Read frozen observations with the current grader; never replace raw evidence."""
import argparse
import hashlib
import json
from pathlib import Path

from .grade import outcome, REVISION


def regrade(trial):
    trial = Path(trial)
    inputs = {}
    hashes = {}
    for name in ("facts.json", "observation.json", "result.json"):
        raw = (trial/name).read_bytes()
        inputs[name] = json.loads(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    observed = inputs["observation.json"]
    original = inputs["result.json"]
    result = outcome(inputs["facts.json"], observed["state"],
                     observed["artifacts"], observed["telemetry"])
    # Preserve the runner's operational error rule, not only its semantic oracle.
    if observed["telemetry"].get("errors") and result["label"] not in ("invalid_fixture", "deadline_censored"):
        result["label"] = "runtime_failure"
    return {"id": original["id"], "grader_revision": REVISION,
            "input_sha256": hashes, "original_grader_revision": original["result"].get("grader_revision"),
            "original_label": original["result"]["label"],
            "label_changed": original["result"]["label"] != result["label"],
            "result": result,
            "limitation": "Derived grading only, no rerun. Fixture disqualification and semantic review remain separate; raw observations and results are unchanged."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trials", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"grader_revision": REVISION, "trials": [regrade(p) for p in args.trials]}
    if args.output:
        # Exclusive creation prevents accidental replacement of another review.
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
        args.output.chmod(0o600)
    else:
        print(json.dumps(report, indent=2))

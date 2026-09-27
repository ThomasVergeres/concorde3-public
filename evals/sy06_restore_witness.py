"""One SY06 dependency witness, not a behavioral trial or a generic repair.

Runs the generated processor unchanged in a network-disabled container. No
cognition, authority credentials, state mutation or original source execution.
Does not qualify notification delivery; the fixture lifecycle checks that path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from evals.trial_replay import load, restore
from worlds.forensics import write_json


def run(snapshot, target):
    snapshot, target = Path(snapshot).resolve(), Path(target).resolve()
    manifest = load(snapshot)
    source = Path(manifest["source"])
    image = manifest["container"]["image"]
    result = {"started": time.time(), "scope": "SY06 generated processor dependency witness",
              "image": image, "model_calls": 0, "qualified": False,
              "limitations": ["Operator restarted program directly, not autonomous recovery.",
                  "No cognition, notification or attention-reuse claim; canonical self remains frozen."]}
    before = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in
              ("subject/.concorde2/state.json", "subject/.concorde2/events.jsonl", "subject/harbor_processor.py", "exchange/inbox.json")}
    restore(snapshot, target)
    subject, exchange = target / "subject", target / "exchange"
    state_bytes = (subject / ".concorde2/state.json").read_bytes()
    history_bytes = (subject / ".concorde2/events.jsonl").read_bytes()
    code = subject / "harbor_processor.py"
    result["program_sha256"] = hashlib.sha256(code.read_bytes()).hexdigest()
    name = "c3-lab-restore-witness-" + uuid.uuid4().hex[:12]
    result["container"] = name
    created = False
    def command(args):
        return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=20).strip()
    try:
        # timeout supplies an in-container terminal condition if this controller
        # dies; finally stops only this named disposable container. No auth mount.
        command(["docker", "create", "--name", name, "--network", "none", "--read-only",
                 "--memory", "256m", "--cpus", "0.5", "--pids-limit", "32", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
                 "--tmpfs", "/tmp:rw,size=16m,mode=1777", "--mount", f"type=bind,source={subject},target=/instance",
                 "--mount", f"type=bind,source={exchange},target=/exchange,readonly", "--entrypoint", "timeout",
                 image, "--signal=TERM", "--kill-after=2s", "45s", "/usr/bin/python3", "/instance/harbor_processor.py"])
        created = True
        command(["docker", "start", name])
        baseline = json.loads((subject / "artifacts/results.json").read_text())
        inbox = json.loads((exchange / "inbox.json").read_text())
        witness_id = "restore-witness-" + uuid.uuid4().hex[:12]
        inbox.append({"source": {"id": witness_id, "revision": 1, "lines": [
            {"sku": "witness-red", "units": 7}, {"sku": "witness-red", "units": 2},
            {"sku": "witness-blue", "units": 4}]}})
        # Atomic ordinary source publication; never edit subject memory or code.
        write_json(exchange / "witness-input.tmp", inbox)
        os.replace(exchange / "witness-input.tmp", exchange / "inbox.json")
        began = time.monotonic()
        expected = {"request_id": witness_id, "revision": 1, "status": "ok",
                    "by_sku": {"witness-red": 9, "witness-blue": 4}, "total_units": 13}
        found = False
        while time.monotonic() - began < 15:
            observed = json.loads((subject / "artifacts/results.json").read_text())
            if expected in observed["results"]:
                found = True
                break
            time.sleep(.1)
        if not found: raise ValueError("restored operation did not deliver witness within 15 seconds")
        if any(row not in observed["results"] for row in baseline["results"]):
            raise ValueError("restored operation lost previous result")
        result.update(qualified=True, receiving_seconds=time.monotonic() - began,
                      prior_results_preserved=len(baseline["results"]))
    finally:
        if created:
            command(["docker", "stop", "-t", "2", name])
            result["container_stopped"] = command(["docker", "inspect", "--format", "{{.State.Running}}", name]) == "false"
        result["canonical_state_unchanged"] = (subject / ".concorde2/state.json").read_bytes() == state_bytes and (subject / ".concorde2/events.jsonl").read_bytes() == history_bytes
        result["source_unchanged"] = all(hashlib.sha256((source / p).read_bytes()).hexdigest() == digest for p, digest in before.items())
        result["finished"] = time.time()
        result["qualified"] = result["qualified"] and result.get("container_stopped", False) and result["canonical_state_unchanged"] and result["source_unchanged"]
        write_json(target / "witness.json", result)
    if not result["qualified"]: raise ValueError("witness did not qualify")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    print(json.dumps(run(args.snapshot, args.output), indent=2))

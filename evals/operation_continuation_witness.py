"""Bounded, no-model qualification of SY07's ordinary format-change exposure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .trial_replay import load, restore
from .lab import save
from .systemization_cases import receive_results


def run(snapshot, target, fixture, image=None):
    snapshot, target, fixture = Path(snapshot).resolve(), Path(target).resolve(), Path(fixture).resolve()
    manifest = load(snapshot)
    source_image = manifest["container"]["image"]
    image = subprocess.check_output(["docker", "image", "inspect", image or source_image,
                                    "--format", "{{.Id}}"], text=True, timeout=20).strip()
    restore(snapshot, target)
    ws, ex = target / "subject", target / "exchange"
    state_path = ws / ".concorde2/state.json"
    original = json.loads(state_path.read_text())
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    save(target / "fork.json", {"snapshot_sha256": digest, "status": "preparing"})
    spec = {"cutoff": time.time()+90, "model": "gpt-5.6-luna", "effort": "xhigh", "starts": 6,
            "source_manifest": digest, "manifest_path": str(target / "fork.json"), "resume_programs": []}
    subprocess.run([str(fixture), "fork", str(ws)], input=json.dumps(spec), text=True, check=True, timeout=20)
    name = "c3-lab-envelope-witness-" + uuid.uuid4().hex[:12]
    result = {"qualified": False, "model_calls": 0, "container": name, "snapshot_sha256": digest,
              "image": image, "source_image": source_image,
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "witness_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Actual generated program emits ordinary error observation; independent public-interface solution can receive",
              "limitations": "Direct operator program launch and assisted repair; no C3 decision, recovered capacity or behavioral improvement claim."}
    def command(args): return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=20).strip()
    created = False
    try:
        command(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", f"{os.getuid()}:{os.getgid()}",
                 "--memory", "256m", "--cpus", "0.5", "--pids-limit", "32", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--tmpfs", "/tmp:rw,size=16m,mode=1777", "--mount", f"type=bind,source={ws},target=/instance",
                 "--mount", f"type=bind,source={ex},target=/exchange,readonly", "--entrypoint", "sleep", image, "90"])
        created = True
        command(["docker", "start", name])
        source = {"id": "envelope-witness", "revision": 1, "lines": [{"sku": "cedar", "units": 3}, {"sku": "cedar", "units": 5}]}
        inbox = json.loads((ex / "inbox.json").read_text())
        save(ex / "inbox.json", {"schema_version": 2, "messages": inbox + [{"from": "Harbor desk", "source": source}]})
        pursuit = original["programs"]["harbor-results-processor"]["intention"]
        command(["docker", "exec", "-d", "--env", "CONCORDE3_INSTANCE=/instance", "--env", "CONCORDE3_PURSUIT="+pursuit,
                 name, "/usr/bin/python3", "/instance/harbor_processor.py"])
        end = time.monotonic()+15
        observations = []
        while time.monotonic() < end:
            state = json.loads(state_path.read_text())
            observations = [w for key,w in state.get("wakes",{}).items() if key not in original.get("wakes",{}) and "inbox must be an array" in w["evidence"]]
            if observations: break
            time.sleep(.1)
        if not observations: raise ValueError("generated program did not expose its format failure through an ordinary observation")
        output = json.loads((ws / "artifacts/results.json").read_text())
        if receive_results(output, source)["matched"]: raise ValueError("unchanged program unexpectedly delivered changed envelope")
        # Independent implementation reads only public source data; never imports
        # generated code or receives private grading facts. This is assisted work.
        independent = """import json,pathlib,os
source=json.loads(pathlib.Path('/exchange/inbox.json').read_text())
messages=source if isinstance(source,list) else source['messages']
path=pathlib.Path('/instance/artifacts/results.json'); result=json.loads(path.read_text())
for message in messages:
 s=message['source']; key=(s['id'],s['revision'])
 if any(r.get('request_id')==s['id'] and r.get('revision',-1)>=s['revision'] for r in result['results']):continue
 totals={}
 for row in s['lines']:
  assert type(row['units']) is int and row['units']>=0
  totals[row['sku']]=totals.get(row['sku'],0)+row['units']
 result['results'].append(dict(request_id=s['id'],revision=s['revision'],status='ok',by_sku=totals,total_units=sum(totals.values())))
temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result));os.replace(temp,path)
"""
        command(["docker", "exec", name, "python3", "-c", independent])
        if not receive_results(json.loads((ws / "artifacts/results.json").read_text()), source)["correct"]:
            raise ValueError("independent ordinary-interface witness failed receiving")
        result.update(qualified=True, error_observation_ids=[w["id"] for w in observations], unchanged_program_received=False, independent_solution_received=True)
    finally:
        if created:
            command(["docker", "exec", name, "concorde3", "freeze", "/instance"])
            command(["docker", "stop", "-t", "2", name])
            result["container_stopped"] = command(["docker", "inspect", "--format", "{{.State.Running}}", name]) == "false"
        final = json.loads(state_path.read_text())
        result["no_new_activations"] = final["activations"] == original["activations"]
        result["mode"] = final["mode"]
        result["qualified"] = result["qualified"] and result.get("container_stopped",False) and result["no_new_activations"] and result["mode"] == "frozen"
        save(target / "witness.json", result)
    if not result["qualified"]: raise ValueError("format-change exposure did not qualify")
    return result


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("snapshot");p.add_argument("output");p.add_argument("fixture")
    p.add_argument("--image", help="Explicit runtime migration qualification; defaults to the original source image")
    args=p.parse_args();print(json.dumps(run(args.snapshot,args.output,args.fixture,args.image),indent=2))

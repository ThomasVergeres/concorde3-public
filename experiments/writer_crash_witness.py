"""No-model fault injection: the writer's owner dies; its independent timer must stop both containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from evals.lab import command, save
from .isolated_writer import IsolatedWriter
from .discrepancy_transport import stop_active


def child(root, image, cutoff):
    source = root/"cases/crash/source"; source.mkdir(parents=True)
    writer = IsolatedWriter(root, "no-model-owner-crash", source, image, cutoff)
    writer.start()
    save(root/"armed.json", {"containers": [writer.worker, writer.proxy],
        "timer": writer.unit+".timer", "cutoff": cutoff, "owner_pid": os.getpid(), "model_dispatched": False})
    os._exit(37)  # Deliberately bypass finally/stop; independent timer is the witness.


def run(root, image):
    if root.exists(): raise ValueError("fresh crash witness root required")
    image = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    root.mkdir(parents=True, mode=0o700)
    cutoff = time.time()+75
    result = {"model_dispatched": False, "image": image,
        "cutoff": cutoff, "scope": "Synthetic owner-death/independent-container-stop witness, not business behavior"}
    result["source_hashes"] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("writer_crash_witness.py", "isolated_writer.py")}
    try:
        process = subprocess.run([sys.executable, "-m", "experiments.writer_crash_witness", str(root),
            "--image", image, "--child-cutoff", str(cutoff)], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=60)
        result["owner_returncode"] = process.returncode
        if process.returncode != 37: raise RuntimeError("owner did not reach intentional crash: "+process.stderr[-1000:])
        armed = json.loads((root/"armed.json").read_text())
        result["inventory_survived_owner_crash"] = (root/"container-active/no-model-owner-crash.json").exists()
        if not result["inventory_survived_owner_crash"]: raise RuntimeError("container ownership inventory lost")
        before = json.loads(command(["docker", "inspect", *armed["containers"]]))
        result["running_after_owner_death"] = [r["State"]["Running"] for r in before]
        if not all(result["running_after_owner_death"]): raise RuntimeError("fault exposure missing")
        while time.time() < cutoff+20:
            inspected = json.loads(command(["docker", "inspect", *armed["containers"]]))
            if not any(r["State"]["Running"] for r in inspected): break
            time.sleep(.5)
        result["observed_at"] = time.time()
        result["container_states_before_operator_cleanup"] = [{"name":r["Name"], "state":r["State"]} for r in inspected]
        result["independent_timer_stopped_both"] = not any(r["State"]["Running"] for r in inspected)
        result["status"] = "passed" if result["independent_timer_stopped_both"] else "failed"
    except Exception as error:
        result.update(status="failed", error=str(error))
    finally:
        # A cleanup success cannot overwrite the independent-timer result above.
        result["cleanup"] = stop_active(root)
        if result["cleanup"]["errors"]: result["status"] = "cleanup_unverified"
        save(root/"result.json", result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("root", type=Path)
    p.add_argument("--image", required=True); p.add_argument("--child-cutoff", type=float)
    a=p.parse_args(); root=a.root.resolve()
    if a.child_cutoff: child(root,a.image,a.child_cutoff)
    else: run(root,a.image)

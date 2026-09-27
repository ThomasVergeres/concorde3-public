"""Subject-side lifecycle only. Contains no evaluator or expected answer."""
import datetime as dt
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import time

def prepare_instance(args):
    mode = args.get("initialization", "seed")
    if mode == "seed":
        subprocess.run(["/run/fixture", "create", "/instance"], input=pathlib.Path("/run/seed.json").read_bytes(), check=True)
    elif mode == "frozen_copy":
        raw = pathlib.Path("/instance/.concorde2/state.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.get("source_state_sha256"):
            raise ValueError("copied state digest mismatch; refusing preparation")
        spec = json.loads(pathlib.Path("/run/fork-spec.json").read_text())
        cutoff = dt.datetime.fromisoformat(args["freeze_at"].replace("Z", "+00:00")).timestamp()
        if spec.get("source_manifest") != args.get("source_snapshot_sha256") or spec.get("cutoff") != cutoff:
            raise ValueError("fork provenance/horizon mismatch")
        subprocess.run(["/run/fixture", "fork", "/instance"], input=json.dumps(spec).encode(), check=True)
    else:
        raise ValueError("unknown initialization; refusing implicit reseed")

def restart_requested(proc, runtime, fault_path, handled, launch):
    """Lab-only fault injection: cancel and resume, never reseed durable truth."""
    try:
        fault = json.loads(pathlib.Path(fault_path).read_text())
    except (OSError, ValueError):
        return proc
    if not isinstance(fault, dict) or fault.get("action") != "restart_runtime":
        return proc
    key = fault.get("id")
    if not isinstance(key, str) or not key or key in handled:
        return proc
    if proc.poll() is not None:
        return proc
    path = runtime / "lab-restarts.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    if not isinstance(rows, list):
        raise RuntimeError("invalid restart history; refusing interruption")
    before = proc.pid
    proc.terminate()
    forced = False
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        # Do not start a second supervisor when teardown could be incomplete.
        proc.kill(); proc.wait(timeout=10)
        raise RuntimeError("runtime interruption did not finish normal teardown")
    restart_at = time.time()
    after = launch()
    rows.append({"id": key, "activation": fault.get("activation"), "at": time.time(),
                 "before_pid": before, "after_pid": after.pid, "restart_at": restart_at,
                 "returncode": proc.returncode, "forced": forced,
                 "kind": "SIGTERM runtime cancellation and fresh process; no reseed"})
    temp = path.with_suffix(".new")
    try:
        temp.write_text(json.dumps(rows)); os.replace(temp, path)
    except Exception:
        after.terminate()
        try:
            after.wait(timeout=15)
        except subprocess.TimeoutExpired:
            after.kill(); after.wait(timeout=10)
        raise
    handled.add(key)
    return after

def main():
    args = json.loads(pathlib.Path("/run/execution.json").read_text())
    absolute_freeze = None
    if "freeze_at" in args:
        value = args["freeze_at"]
        if not isinstance(value, str) or not value:
            raise ValueError("freeze_at must be a timezone-aware timestamp")
        freeze = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if freeze.tzinfo is None:
            raise ValueError("freeze_at must include its timezone")
        absolute_freeze = freeze.timestamp()
    os.makedirs("/home/node/.codex", exist_ok=True)
    preflight = model_preflight(args)
    prepare_instance(args)
    # The advertised runtime horizon is authoritative, not a fresh wall budget
    # starting after login/seed work. A monotonic guard also prevents a backward
    # wall-clock adjustment from extending this allocation. Legacy captures did
    # not supply freeze_at and retain their original wall_seconds behavior.
    remaining = (max(0.0, absolute_freeze - time.time()) if absolute_freeze is not None
                 else args["wall_seconds"])
    deadline = time.monotonic() + remaining
    def cutoff_reason():
        if absolute_freeze is not None and time.time() >= absolute_freeze:
            return "absolute_cutoff"
        if time.monotonic() >= deadline:
            return "monotonic_guard" if absolute_freeze is not None else "legacy_wall_cutoff"
        return None
    runtime = pathlib.Path("/instance/.concorde2")
    (runtime/"lab-start.json").write_text(json.dumps({"at":time.time(),**preflight,
        "freeze_at":args.get("freeze_at"),"remaining_seconds":remaining,
        "cutoff_source":"configured_freeze_at" if absolute_freeze is not None else "legacy_wall_seconds"}))
    one = args["entry"]=="phase_probe" or (args["starts"]==1 and not args.get("maintain_until_deadline"))
    proc = None
    handled_faults = set()
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    def launch_replacement():
        # Teardown can consume seconds after the polling guard. Recheck at
        # the actual launch boundary, after the old supervisor has stopped.
        if stopping or cutoff_reason() is not None:
            raise RuntimeError("runtime restart refused after stop/cutoff")
        return subprocess.Popen(["concorde3", "run", "/instance"])
    reason = None
    try:
        reason = cutoff_reason()
        if reason is None:
            proc = subprocess.Popen(["concorde3","pulse" if one else "run","/instance"])
        while proc is not None and proc.poll() is None and not stopping:
            reason = cutoff_reason()
            if reason is not None:
                break
            time.sleep(min(.5, max(0.0, deadline - time.monotonic())))
            # Do not start a replacement supervisor after the cutoff or an
            # operator stop delivered during the polling sleep.
            reason = "requested_stop" if stopping else cutoff_reason()
            if reason is not None:
                break
            if args.get("allow_runtime_interruption"):
                proc = restart_requested(proc, runtime, "/exchange/runtime-fault.json", handled_faults,
                                         launch_replacement)
            try:
                state = json.loads((runtime/"state.json").read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            acts = [a for key,a in state["activations"].items() if key!="act.checkpoint" and a.get("usage",{}).get("basis")!="constructed"]
            if args["entry"] == "phase_probe" and acts and not any(a["status"]=="running" for a in acts):
                reason = "phase_probe_complete"
                break
            if args["entry"] == "episode" and not args.get("maintain_until_deadline") and len(acts)>=args["starts"] and not any(a["status"]=="running" for a in acts):
                reason = "starts_complete"
                break
    finally:
        # Kernel freeze first; parent/container/watchdog still enforce termination.
        reason = reason or ("requested_stop" if stopping else cutoff_reason()) or "runtime_exit"
        stopped = {"stop_reason":reason,"requested_at":time.time(),
                   "freeze_at":args.get("freeze_at"),"freeze_completed":False}
        try:
            subprocess.run(["concorde3","freeze","/instance"], check=True, timeout=15)
            stopped["freeze_completed"] = True
        finally:
            try:
                if proc is not None:
                    try: proc.wait(timeout=10)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            finally:
                # Cleanup grace is not additional admission time. Early phase
                # probes and quota completions remain explicit, legitimate stops.
                stopped["finished_at"] = time.time()
                (runtime/"lab-stop.json").write_text(json.dumps(stopped))

def model_preflight(args):
    if args.get("model_transport", "subscription") == "explicit_api":
        model = args.get("model")
        if model != "muse-spark-1.3-contributor": raise RuntimeError("unknown experimental API model")
        caps = json.loads(subprocess.check_output(["python3", "/run/muse.py", "--concorde-capabilities"], text=True, timeout=10))
        if caps.get("protocol") != 2 or caps.get("continuation") is not True:
            raise RuntimeError("API adapter continuation preflight failed")
        return {"auth_basis":"explicit_api", "model":model, "protocol":2, "continuation":True,
                "credentials":"host broker only; no model credential mounted into subject"}
    if args.get("model_transport", "subscription") != "subscription":
        raise RuntimeError("unknown transport; no fallback")
    os.symlink("/run/subscription-auth.json", "/home/node/.codex/auth.json")
    login = subprocess.run(["codex","login","status"], capture_output=True, text=True, timeout=20)
    if login.returncode or "Logged in using ChatGPT" not in login.stdout + login.stderr:
        raise RuntimeError("subscription preflight failed; no API fallback")
    return {"auth_basis":"subscription", "codex_version":subprocess.check_output(["codex","--version"],text=True).strip()}


if __name__ == "__main__":
    main()

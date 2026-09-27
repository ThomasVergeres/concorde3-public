"""No-model MR01 runtime/receiving qualification in a disposable isolated copy.

Generated code runs only inside the no-network container. Direct program launches
and a manufactured partner reply are assisted interface witnesses, not behavior.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .adaptation_observe import read_attachment
from .lab import save
from .memory_consequence import PROGRAMS, append_message, materialize_memory
from .systemization_cases import receive_results
from .trial_replay import load, restore


def run(snapshot, target, fixture, image):
    snapshot, target, fixture = (Path(p).resolve() for p in (snapshot, target, fixture))
    manifest = load(snapshot)
    def command(args):
        return subprocess.check_output([str(x) for x in args], text=True, stderr=subprocess.PIPE, timeout=25).strip()
    image = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    restore(snapshot, target)
    ws, ex = target / "subject", target / "exchange"
    original = json.loads((ws / ".concorde2/state.json").read_text())
    accepted = (ws / "artifacts/accepted.json").read_bytes()
    _, _, exchange, facts = materialize_memory(snapshot, "challenge", "situated", 201)
    for name, value in exchange.items(): save(ex / name, value)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    save(target / "fork.json", {"snapshot_sha256": digest, "status": "preparing"})
    spec = {"cutoff": time.time() + 120, "model": "gpt-5.6-luna", "effort": "xhigh", "starts": 4,
            "source_manifest": digest, "manifest_path": str(target / "fork.json"), "resume_programs": list(PROGRAMS)}
    subprocess.run([str(fixture), "fork", str(ws)], input=json.dumps(spec), text=True, check=True, timeout=25)
    name = "c3-lab-memory-witness-" + uuid.uuid4().hex[:12]
    result = {"family": "MR01", "qualified": False, "model_calls": 0, "container": name,
        "image": image, "source_image": manifest["container"]["image"], "snapshot_sha256": digest,
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "witness_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Same lived program reads schema-v2 batches and ignores free-form inquiry; independent receiving and observation path",
        "limitations": "Direct operator launches and manufactured reply, accelerated witness only; no model decision or native admission evidence."}
    created = False
    try:
        command(["docker", "create", "--name", name, "--network", "none", "--read-only",
            "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "256m", "--cpus", "0.5",
            "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,size=16m,mode=1777", "--mount", f"type=bind,source={ws},target=/instance",
            "--mount", f"type=bind,source={ex},target=/exchange,readonly", "--entrypoint", "sleep", image, "120"])
        created = True
        command(["docker", "start", name])
        for program in PROGRAMS:
            command(["docker", "exec", "-d", "--env", "CONCORDE3_INSTANCE=/instance", "--env",
                     "CONCORDE3_PURSUIT=" + original["programs"][program]["intention"], name,
                     *original["programs"][program]["command"]])
        inbox = json.loads((ex / "inbox.json").read_text())
        received = []
        for event in facts["schedule"]:
            message = event["message"]
            inbox = append_message(inbox, message)
            save(ex / "inbox.json", inbox)
            if "source" not in message:
                continue
            end = time.monotonic() + 15
            okay = False
            while time.monotonic() < end:
                output = json.loads((ws / "artifacts/results.json").read_text())
                okay = receive_results(output, message["source"])["correct"]
                if okay: break
                time.sleep(.1)
            received.append({"id": message["source"]["id"], "revision": message["source"]["revision"], "correct": okay})
            if not okay: raise ValueError("unchanged captured program did not produce independently correct receiving")
        # This is deliberately a freely worded decline, not an expected answer.
        reply = "We cannot commit a second desk without agreeing its scope and coverage. Can you describe the expected burst volume?"
        command(["docker", "exec", name, "python3", "-c",
                 "from pathlib import Path; Path('/instance/artifacts/partner-reply.md').write_text(" + repr(reply) + ")"])
        observed = read_attachment(ws, "artifacts/partner-reply.md")
        state = json.loads((ws / ".concorde2/state.json").read_text())
        changed = {p: state["programs"][p].get("observed", {}) != original["programs"][p].get("observed", {}) for p in PROGRAMS[1:]}
        result.update(unchanged_program_received=all(r["correct"] for r in received), receiving=received,
            partner_reply_visible=observed.get("text") == reply, observation_changes=changed,
            ordinary_inbox_observed=changed["watch-aster-session-inbox"],
            accepted_preserved=(ws / "artifacts/accepted.json").read_bytes() == accepted)
        if not changed["watch-aster-session-inbox"]:
            raise ValueError("ordinary inherited inbox observation did not record the changed input")
        result["qualified"] = True
    finally:
        # Stopping the isolated container is independent of a freeze command's
        # success. Always retain teardown errors, never leave escaped witnesses.
        cleanup = []
        if created:
            try: command(["docker", "exec", name, "concorde3", "freeze", "/instance"])
            except Exception as error: cleanup.append({"action": "freeze", "error": str(error), "stderr": getattr(error, "stderr", None)})
            try:
                command(["docker", "stop", "-t", "2", name])
                result["container_stopped"] = command(["docker", "inspect", name, "--format", "{{.State.Running}}"]) == "false"
            except Exception as error: cleanup.append({"action": "stop", "error": str(error), "stderr": getattr(error, "stderr", None)})
        final = json.loads((ws / ".concorde2/state.json").read_text())
        result.update(no_new_activations=final["activations"] == original["activations"], mode=final["mode"], cleanup_errors=cleanup)
        result["qualified"] = (result["qualified"] and result.get("container_stopped", False)
            and result["no_new_activations"] and result["mode"] == "frozen" and not cleanup)
        save(target / "witness.json", result)
    if not result["qualified"]: raise ValueError("MR01 runtime witness did not qualify")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("snapshot", "output", "fixture", "image"): parser.add_argument(arg)
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output, args.fixture, args.image), indent=2))

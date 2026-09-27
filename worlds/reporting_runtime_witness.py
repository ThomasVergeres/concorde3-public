"""No-model selected-subject container boundary witness, not a live C3 trial."""
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch

from . import campaign
from .forensics import write_json
from .reporting_controller import poll, prepare


def disabled_cognition_command(args):
    """Keep actual deployment mounts/network, replace only the cognitive entrypoint."""
    if args[:3] == ["docker", "run", "-d"]:
        name = args[args.index("--name") + 1] if "--name" in args else ""
        if name.endswith("-world") and ["-m", "worlds.cli", "serve"] == args[-8:-5]:
            return args
        if name.endswith(("-transport", "-research")) and args[-1] in ("/transport.py", "/research.py"):
            return args
        if not name.endswith("-steward") or args[-1] != "/subject.py" or args[-4:-2] != ["--entrypoint", "python3"]:
            raise ValueError("unrecognized subject launch; no cognition dispatch")
        return args[:-1] + ["-c", "import time; time.sleep(180)"]
    return args


def exercise(world, source, rules, image):
    proof = {"qualified": False, "errors": [], "closure_errors": [],
             "meaning": "Actual deployment boundaries with substituted sleep; no model, preflight or cognitive activation."}
    original_command = campaign.command
    deployment = None
    original_states = {p: p.read_bytes() for p in (world.s.root / "subjects").glob("*/.concorde2/state.json")}
    replaced = []

    def command(args, **kwargs):
        safe = disabled_cognition_command(args)
        if safe != args:
            replaced.append(args[args.index("--name") + 1])
        return original_command(safe, **kwargs)

    def execute(container, script):
        return json.loads(original_command(["docker", "exec", container, "python3", "-c", script]))

    try:
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg["counterpart_mode"] = "scripted"
            world.s.meta(db, "config", cfg)
            world.s.event(db, "_operator", "reporting_runtime_witness", {
                "substitution": "Selected subject's Python entrypoint sleeps; original mounts/network retained. No C3 run."})
        # Normal selected deployment installs its independent cutoff before any
        # container. Other copied selves are already explicitly frozen.
        with patch.object(campaign, "command", side_effect=command):
            deployment = campaign.launch(world, image=image, subjects=["steward"])
        container = deployment["subjects"]["steward"]
        if replaced != [container] or set(deployment["inactive_subjects"]) != {"reach", "frontier"}:
            raise AssertionError("unexpected runtime selection or entrypoint")
        info = json.loads(original_command(["docker", "inspect", container]))[0]
        mounts = {m["Destination"]: m for m in info["Mounts"] if m["Type"] == "bind"}
        expected = {"/instance", "/home/node/.codex/sessions", "/world/access.json", "/world/client.py",
                    "/world/codex_meter.py", "/subject.py", "/run/subscription-auth.json"}
        if set(mounts) != expected or Path(mounts["/instance"]["Source"]).resolve() != world.s.root.resolve() / "subjects/steward":
            raise AssertionError("unexpected subject mount inventory")
        if any(m["RW"] for key, m in mounts.items() if key not in {"/instance", "/home/node/.codex/sessions"}):
            raise AssertionError("runtime-only input unexpectedly writable")
        proof["container"] = container
        proof["image"] = info["Image"]
        proof["mount_destinations"] = sorted(mounts)
        proof["isolation"] = execute(container, """import json,pathlib,socket
for p in ('/data/world.sqlite','/code/worlds/reporting_controller.py','/world-budget/calls.jsonl',
          '/world/reporting_controller.py','/var/run/docker.sock','/instance/../reach/.concorde2/state.json'):
 assert not pathlib.Path(p).exists(),p
assert pathlib.Path('/world/client.py').is_file()
print(json.dumps({'private_host_paths_absent':True,'participant_client_present':True}))
""")
        now = time.time()
        state = prepare(world, variant="retiring", source=source, rules=rules,
                        supplier_until=now+45, source_at=now+60, deadline=now+90, end_at=now+110)
        state = poll(world)
        private_id = state["receipts"]["supplier-rules"]["result"]["id"]
        terms_id = state["receipts"]["supplier-terms"]["result"]["id"]
        # Never read or print auth material. Ordinary client supplies its own
        # credential internally; output contains only exposure/denial conclusions.
        script = "import sys,json,time;sys.path.insert(0,'/world');import client\n"
        script += "for attempt in range(20):\n try:\n  view=client.request();break\n except Exception:\n  if attempt==19:raise\n  time.sleep(.2)\n"
        script += f"assert {source['batch']!r} not in json.dumps(view)\n"
        script += f"terms=client.request('actions',{{'op':'inspect','id':{terms_id!r}}},'runtime-witness:terms')['result']\n"
        script += "assert terms['content']['export_terms']\n"
        script += "denied={}\n"
        script += f"for name,section,data,key in [('mapping','actions',{{'op':'inspect','id':{private_id!r}}},'runtime-witness:private'),('controller','reporting_dependency_controller',None,None)]:\n"
        script += " try:\n  client.request(section,data,key)\n except RuntimeError as error:\n  denied[name]=str(error)\n else:raise AssertionError('private information accessible: '+name)\n"
        script += "print(json.dumps({'ordinary_request_visible':any('working client report' in m.get('text','') for m in view['records']['message']), 'new_source_absent':True,'terms_visible':True,'denied':denied}))"
        proof["participant_exposure"] = execute(container, script)
        if not proof["participant_exposure"]["ordinary_request_visible"]:
            raise AssertionError("actual client overview did not expose the ordinary request")
        for name, expected_error in (("mapping", "record unavailable"), ("controller", "unknown public section")):
            if json.loads(proof["participant_exposure"]["denied"][name]).get("error") != expected_error:
                raise AssertionError("transport/unknown error is not evidence of a privacy denial")
        status = json.loads(original_command(["docker", "exec", container, "concorde3", "status", "/instance"]))
        proof["attention_inventory"] = {k: status[k] for k in ("mode", "sequence", "attention", "starts", "freeze_at")}
        proof["attention_limit"] = "Read-only native status, not an admitted activation or demonstrated future wake."
        proof["pre_shutdown_unchanged"] = all(p.read_bytes() == raw for p, raw in original_states.items())
        if not proof["pre_shutdown_unchanged"]:
            raise AssertionError("runtime boundary witness changed a copied self before freeze")
        with world.s.transaction() as db:
            started = json.loads((world.s.root / "fork.json").read_text())["started"]
            proof["new_model_calls"] = db.execute("SELECT count(*) FROM calls WHERE at>=?", (started,)).fetchone()[0]
        if proof["new_model_calls"]:
            raise AssertionError("unexpected model dispatch")
        proof["qualified"] = True
    except BaseException as error:
        proof["errors"].append(str(error))
        raise
    finally:
        if deployment:
            try:
                campaign.freeze(world)
                proof["containers_stopped"] = not original_command([
                    "docker", "ps", "--filter", "label=concorde.world="+campaign.world_id(world.s.root), "--format", "{{.Names}}"])
                if not proof["containers_stopped"]:
                    raise AssertionError("witness containers remain live")
                proof["deployment_status"] = json.loads((world.s.root / "deployment.json").read_text())["status"]
            except Exception as error:
                proof["closure_errors"].append(str(error))
        proof["qualified"] = proof["qualified"] and not proof["errors"] and not proof["closure_errors"] and proof.get("containers_stopped", False)
        proof["witness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        write_json(world.s.root / "reporting-runtime-proof.json", proof)
    if not proof["qualified"]:
        raise ValueError("runtime boundary witness did not qualify")
    return proof

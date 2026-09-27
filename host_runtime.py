"""Shared host-side process, file, network, and session helpers.

Keep this module independent of the evaluation and world packages. It is used
by host controllers, not by the world gateway bundled into a container.
"""

import ipaddress
import json
import os
from pathlib import Path
import random
import subprocess


def command(args, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, check=True, timeout=kwargs.pop("timeout",60), **kwargs).stdout.strip()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+".new")
    with tmp.open("w") as f:
        json.dump(value,f,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)


def create_network(name, *, isolate_host=False, run=command):
    # Docker's default pools are already nearly consumed by historical trials.
    # Pick a small subnet that overlaps neither a route nor another Docker net.
    routes=json.loads(run(["ip","-j","-4","route"]))
    used=[ipaddress.ip_network(r["dst"],strict=False) for r in routes if r.get("dst") not in (None,"default")]
    slots=list(range(16384))
    random.SystemRandom().shuffle(slots)
    for slot in slots:
        subnet=ipaddress.ip_network((int(ipaddress.ip_address("10.243.0.0"))+slot*8,29))
        if any(subnet.overlaps(n) for n in used): continue
        options = (["--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                    "--opt", "com.docker.network.bridge.gateway_mode_ipv6=isolated"] if isolate_host else [])
        try: return run(["docker","network","create","--internal",*options,"--subnet",str(subnet),name])
        except subprocess.CalledProcessError as e:
            if "overlap" not in e.stderr.lower(): raise
    raise RuntimeError("no nonoverlapping lab subnet available")


def prepare_session_storage(instance):
    """Persist only private session records; authentication stays ephemeral."""
    instance = Path(instance)
    runtime = instance / ".concorde2"
    sessions = runtime / "harness-sessions"
    for path in (instance, runtime, sessions):
        if path.is_symlink():
            raise ValueError("session storage must not traverse a symlink")
    runtime.mkdir(mode=0o700, exist_ok=True)
    sessions.mkdir(mode=0o700, exist_ok=True)
    if not sessions.is_dir():
        raise ValueError("session storage must be a private directory")
    sessions.chmod(0o700)
    return sessions

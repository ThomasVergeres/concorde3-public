"""Instance-side command wrapper: unchanged login, metered model invocations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import datetime
import urllib.request
import urllib.error


def request(path, value):
    cfg = json.loads(Path("/world/access.json").read_text())
    req = urllib.request.Request(cfg["url"]+path, data=json.dumps(value).encode(), headers={"Authorization": "Bearer "+cfg["token"], "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode(errors="replace")
        raise RuntimeError(f"World {path} HTTP {error.code}: {detail}") from error


def admission_identity(state, activation):
    seen = set()
    while state["activations"][activation].get("recovery_of"):
        if activation in seen:
            raise RuntimeError("cyclic recovery lineage")
        seen.add(activation)
        activation = state["activations"][activation]["recovery_of"]
    return activation


def main():
    args = sys.argv[1:]
    if "exec" not in args:
        os.execvp("codex", ["codex", *args])
    activation = os.environ["CONCORDE3_ACTIVATION"]
    state = json.loads(Path("/instance/.concorde2/state.json").read_text())
    a = state["activations"][activation]
    admission = admission_identity(state, activation)
    remaining = datetime.datetime.fromisoformat(a["deadline"].replace("Z", "+00:00")).timestamp()-time.time()
    end = time.monotonic()+max(1, min(150, remaining-30))
    request_id = uuid.uuid4().hex
    while True:
        try:
            permit = request("/runtime/reserve", {"activation": admission, "execution": activation, "phase": a["phase"], "request_id": request_id})
            if time.time() >= permit["deadline"]:
                raise RuntimeError("admission expired before execution")
            break
        except Exception:
            if time.monotonic() >= end:
                raise
            time.sleep(2)
    code = 1
    try:
        code = subprocess.call(["codex", *args])
    finally:
        request("/runtime/finish", {"id": permit["id"], "exit_code": code})
    raise SystemExit(code)


if __name__ == "__main__": main()

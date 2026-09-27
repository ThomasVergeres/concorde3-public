"""Meter explicit API work/rectification through existing World admissions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from codex_meter import admission_identity, request


def main():
    adapter = ["python3", "/world/muse.py", *sys.argv[1:]]
    if "--concorde-capabilities" in sys.argv:
        os.execvp(adapter[0], adapter)
    activation = os.environ["CONCORDE3_ACTIVATION"]
    state = json.loads(Path("/instance/.concorde2/state.json").read_text())
    record = state["activations"][activation]
    permit = request("/runtime/reserve", {"activation": admission_identity(state, activation),
                     "phase": record["phase"], "request_id": uuid.uuid4().hex})
    if time.time() >= permit["deadline"]:
        raise RuntimeError("API admission expired before execution")
    code = 1
    try:
        code = subprocess.call(adapter)
    finally:
        request("/runtime/finish", {"id": permit["id"], "exit_code": code})
    raise SystemExit(code)


if __name__ == "__main__": main()

"""Explicit API company preflight; never reads subscription authentication."""
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    state = json.loads(Path("/instance/.concorde2/state.json").read_text())
    cfg = state["config"]
    if cfg["harness"] != "command" or cfg["model"] != "muse-spark-1.3-contributor":
        raise RuntimeError("explicit API profile required")
    result = subprocess.run([*cfg["command"], "--concorde-capabilities"], capture_output=True, timeout=10)
    caps = json.loads(result.stdout)
    if result.returncode or caps.get("protocol") != 2 or not caps.get("continuation"):
        raise RuntimeError("API adapter continuation unavailable")
    Path("/instance/.concorde2/company-preflight.json").write_text(json.dumps({
        "at": time.time(), "auth": "explicit Meta API broker", "model": cfg["model"],
        "continuation": True, "limitation": "Local protocol preflight; actual authenticated inference separately metered."}))
    os.execvp("concorde3", ["concorde3", "run", "/instance"])


if __name__ == "__main__": main()

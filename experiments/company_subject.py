"""Container-side authentication/preflight. Core still owns the full lifecycle."""
import json
from pathlib import Path
import os
import subprocess
import time


def main():
    Path("/home/node/.codex").mkdir(exist_ok=True)
    Path("/home/node/.codex/auth.json").symlink_to("/run/subscription-auth.json")
    check = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=20)
    if check.returncode or "Logged in using ChatGPT" not in check.stdout + check.stderr:
        raise RuntimeError("subscription authentication unavailable; no API fallback")
    Path("/instance/.concorde2/company-preflight.json").write_text(json.dumps({
        "at": time.time(), "auth": "ChatGPT subscription",
        "codex": subprocess.check_output(["codex", "--version"], text=True).strip()}))
    os.execvp("concorde3", ["concorde3", "run", "/instance"])


if __name__ == "__main__":
    main()

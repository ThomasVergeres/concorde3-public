"""Minimal isolated subscription invocation. No world truth, evaluator or DB mount."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    home = Path("/home/node/.codex")
    home.mkdir(exist_ok=True)
    (home/"auth.json").symlink_to("/run/subscription-auth.json")
    check = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=20)
    if check.returncode or "Logged in using ChatGPT" not in check.stdout+check.stderr:
        raise RuntimeError("subscription authentication required; no API fallback")
    config = json.loads(Path("/run/model.json").read_text())
    command = ["codex", "--config", 'forced_login_method="chatgpt"', "--config", 'model_provider="openai"',
               "--config", 'features.apps=false', "--config", 'features.skills=false', "--config", 'features.multi_agent=false',
               "--config", 'features.shell_tool=false', "--config", 'memories.use_memories=false',
               "--config", 'memories.generate_memories=false', "--config", 'model_reasoning_effort='+json.dumps(config["effort"]),
               "--ask-for-approval", "never", "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
               "--json", "--model", config["model"], "--sandbox", "read-only", "--output-schema", "/run/schema.json",
               "--output-last-message", "/tmp/result.json", "-"]
    result = subprocess.run(command, input=sys.stdin.read(), capture_output=True, text=True, timeout=config["timeout"])
    # Logs contain only this actor's context; never emit authentication preflight material.
    print(json.dumps({"returncode": result.returncode, "events": result.stdout, "stderr": result.stderr[-2000:],
                      "result": json.loads(Path("/tmp/result.json").read_text()) if Path("/tmp/result.json").exists() else None}))


if __name__ == "__main__": main()

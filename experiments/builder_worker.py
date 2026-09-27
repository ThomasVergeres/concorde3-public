"""Subscription writer entrypoint; runs only inside the externally isolated image."""
import os
from pathlib import Path
import subprocess


def command():
    return ["codex", "--config", 'forced_login_method="chatgpt"', "--config", 'model_provider="openai"',
        "--config", 'model_reasoning_effort="xhigh"', "--config", "features.apps=false",
        "--config", "features.skills=false", "--config", "features.multi_agent=false",
        "--config", "memories.use_memories=false", "--config", "memories.generate_memories=false",
        "--ask-for-approval", "never", "exec", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--json", "--model", "gpt-5.6-sol", "--sandbox", "danger-full-access", "-"]


def main():
    if os.environ.get("CONCORDE_ISOLATED_WRITER") != "1" or not Path("/.dockerenv").is_file():
        raise RuntimeError("writer entrypoint requires the externally isolated container")
    home = Path("/home/node/.codex")
    home.mkdir(exist_ok=True)
    (home/"auth.json").symlink_to("/run/subscription-auth.json")
    check = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=20)
    if check.returncode or "Logged in using ChatGPT" not in check.stdout+check.stderr:
        raise RuntimeError("subscription writer authentication required; no API fallback")
    # Exec preserves JSON events and the outer Docker process lifetime. The CLI
    # can write only the mounted candidate/session directories and ephemeral tmp.
    os.execvp("codex", command())


if __name__ == "__main__": main()

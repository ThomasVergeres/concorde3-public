"""Subscription Luna 6 max, released only after transport qualification."""
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    runtime = Path('/instance/.concorde2')
    auth_dir = Path('/home/node/.codex')
    auth_dir.mkdir(exist_ok=True)
    (auth_dir / 'auth.json').symlink_to('/run/subscription-auth.json')
    check = subprocess.run(['codex', 'login', 'status'], capture_output=True, text=True, timeout=20)
    if check.returncode or 'Logged in using ChatGPT' not in check.stdout + check.stderr:
        raise RuntimeError('Subscription authentication required')
    until = time.time() + 600
    while not (runtime / 'business-release.json').exists():
        if time.time() >= until:
            raise RuntimeError('Qualification release missing')
        time.sleep(1)
    cfg = json.loads((runtime / 'state.json').read_text())['config']
    if (cfg['model'], cfg['effort'], cfg['starts_per_hour']) != ('gpt-6-luna', 'max', 60):
        raise RuntimeError('Expected Luna 6 max with 60 starts/hour')
    os.execvp('concorde3', ['concorde3', 'run', '/instance'])


if __name__ == '__main__':
    main()

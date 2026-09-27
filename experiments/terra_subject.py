"""One-model private business; wait for qualification before ordinary C3 run."""
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    home=Path('/home/node/.codex');home.mkdir(exist_ok=True)
    (home/'auth.json').symlink_to('/run/subscription-auth.json')
    check=subprocess.run(['codex','login','status'],capture_output=True,text=True,timeout=20)
    if check.returncode or 'Logged in using ChatGPT' not in check.stdout+check.stderr:
        raise RuntimeError('Subscription login required; no API fallback')
    runtime=Path('/instance/.concorde2')
    until=time.time()+600
    while not (runtime/'business-release.json').exists():
        if time.time()>until:raise RuntimeError('Qualification release missing')
        time.sleep(1)
    state=json.loads((runtime/'state.json').read_text())
    if (state['config']['model'],state['config']['effort']) != ('gpt-5.6-terra','medium'):
        raise RuntimeError('Whole activation must use Terra medium')
    os.execvp('concorde3',['concorde3','run','/instance'])


if __name__=='__main__':main()

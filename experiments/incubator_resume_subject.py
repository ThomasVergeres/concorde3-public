"""Explicit operator continuation, not a new founding or automatic reroll."""
from pathlib import Path
import os
import subprocess
import time

def main():
    auth=Path('/home/node/.codex/auth.json')
    auth.parent.mkdir(exist_ok=True)
    auth.symlink_to('/run/subscription-auth.json')
    check=subprocess.run(['codex','login','status'],capture_output=True,text=True,timeout=20)
    if check.returncode or 'Logged in using ChatGPT' not in check.stdout+check.stderr:
        raise RuntimeError('Subscription login required')
    end=time.time()+900
    while not Path('/instance/.concorde2/incubator-resume-release.json').exists():
        if time.time()>end:raise RuntimeError('Operator continuation was not released')
        time.sleep(1)
    os.execvp('concorde3',['concorde3','run','/instance'])

if __name__=='__main__':main()

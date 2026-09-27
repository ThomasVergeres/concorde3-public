"""One complete activation, including rectification, behind an operator gate."""
import json
import subprocess
import time
from pathlib import Path

def main():
    auth = Path('/home/node/.codex/auth.json')
    auth.symlink_to('/run/subscription-auth.json')
    check = subprocess.run(['codex', 'login', 'status'], capture_output=True, text=True, timeout=20)
    if check.returncode or 'Logged in using ChatGPT' not in check.stdout+check.stderr:
        raise RuntimeError('Subscription required; no API fallback')
    end = time.time()+300
    while not Path('/instance/.concorde2/historical-release.json').exists():
        if time.time()>end: raise RuntimeError('Qualification gate not released')
        time.sleep(1)
    result = subprocess.run(['concorde3','pulse','/instance'])
    Path('/instance/.concorde2/historical-finished.json').write_text(json.dumps({'at':time.time(),'exit_code':result.returncode}))
    raise SystemExit(result.returncode)

if __name__=='__main__': main()

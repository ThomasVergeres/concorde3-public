"""Experiment-only founding embodiment; canonical C3 state records both models."""
import json
import os
from pathlib import Path
import subprocess
import time


def founding_result(state):
    rows=list(state['activations'].values())
    if len(rows)!=1 or rows[0]['config']['model']!='gpt-6-astra':
        raise RuntimeError('Expected exactly one recorded Astra founding activation')
    a=rows[0]
    if a['status']!='completed' or not a.get('completion') or a['usage'].get('basis')!='subscription':
        raise RuntimeError('Founding activation did not durably complete; no automatic retry/substitution')
    return a['id']


def main():
    home=Path('/home/node/.codex');home.mkdir(exist_ok=True)
    (home/'auth.json').symlink_to('/run/subscription-auth.json')
    check=subprocess.run(['codex','login','status'],capture_output=True,text=True,timeout=20)
    if check.returncode or 'Logged in using ChatGPT' not in check.stdout+check.stderr:
        raise RuntimeError('Subscription login required; no API fallback')
    runtime=Path('/instance/.concorde2')
    # Host tests the real container boundary before permitting any cognition.
    until=time.time()+300
    while not (runtime/'incubator-release.json').exists():
        if time.time()>until:raise RuntimeError('Isolation release missing; no model dispatched')
        time.sleep(1)
    subprocess.run(['concorde3','pulse','/instance'],check=True)
    state=json.loads(subprocess.check_output(['concorde3','status','/instance']))
    founding=founding_result(state)
    subprocess.run(['concorde3','configure','/instance',json.dumps({'model':'gpt-5.6-luna','effort':'xhigh','deadline_seconds':600,'starts_per_hour':6})],check=True)
    # Normal operation waits for controller attachment, retaining a paused checkpoint.
    (runtime/'incubator-founded.json').write_text(json.dumps({'activation':founding,'at':time.time(),'next_model':'gpt-5.6-luna','next_effort':'xhigh'}))
    until=time.time()+600
    while not (runtime/'incubator-continue.json').exists():
        if time.time()>until:raise RuntimeError('Controller did not acknowledge founding; self remains paused')
        time.sleep(1)
    os.execvp('concorde3',['concorde3','run','/instance'])


if __name__=='__main__':main()

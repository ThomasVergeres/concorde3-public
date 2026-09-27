"""Operator-only environment handoff; subjects and their graphs are untouched."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from . import private_incubator as inc
from worlds.engine import World

def update(root,unit):
    root=Path(root).resolve();m=inc.read(root/'cohort.json')
    prefix='c3-incubator-'+hashlib.sha256(str(root).encode()).hexdigest()[:10]
    if unit not in (prefix+'-resumed.service',prefix+'-environment.service'):raise ValueError('Wrong controller scope')
    if m['status']!='running' or time.time()>m['cutoff']-600:raise ValueError('Live unexpired cohort required')
    os.environ['WORLD_BUDGET_FILE']=str(root/'world-budget/calls.jsonl')
    folder=root/'interventions'/('customer-work-'+str(int(time.time())));folder.mkdir(parents=True,mode=0o700)
    inc.save(folder/'cohort-before.json',m)
    inc.log(root,'environment_update_started',checkpoint=str(folder),reason='Owner requests broader customer work and receiving evidence; C3 unchanged')
    # Pause new counterpart dispatch, allowing already admitted episodes to settle.
    for location in m['worlds'].values():
        w=World(location)
        with w.s.transaction() as db:
            cfg=w.s.meta(db,'config');cfg['counterpart_mode']='scripted';w.s.meta(db,'config',cfg)
            w.s.event(db,'_operator','counterpart_dispatch_paused',{'reason':'Drain for environment-only update; no subject pause'})
    end=time.time()+300
    while time.time()<end:
        pending=0
        for location in m['worlds'].values():
            w=World(location)
            with w.s.transaction() as db:
                pending+=db.execute("SELECT count(*) FROM calls WHERE category!='subject' AND status IN ('reserved','running','uncertain')").fetchone()[0]
        if pending==0:break
        time.sleep(2)
    # Avoid terminal freeze: all self processes remain running. Independent cutoff
    # watchdogs remain armed even if the replacement controller cannot attach.
    stopped=subprocess.run(['sudo','-n','systemctl','kill','--kill-who=all','--signal=SIGKILL',unit],capture_output=True,text=True)
    time.sleep(1)
    if inc.command(['systemctl','show',unit,'--property=MainPID','--value'])!='0':raise RuntimeError('Old controller still owns cohort')
    inc.log(root,'environment_controller_handoff',pending_calls=pending,kill_returncode=stopped.returncode)
    for name,location in m['worlds'].items():
        w=World(location);world=Path(location);dep=inc.read(world/'deployment.json')
        inc.save(folder/(name+'-deployment-before.json'),dep)
        for filename in ('engine.py','contracts.py','experience.py','driver.py'):
            source=inc.REPO/'worlds'/filename;target=world/'assets/worlds'/filename
            if target.exists():shutil.copyfile(target,folder/(name+'-'+filename))
            temporary=target.with_suffix('.upgrade');shutil.copyfile(source,temporary);temporary.replace(target)
        with w.s.transaction() as db:
            cfg=w.s.meta(db,'config');cfg['counterpart_mode']='model';cfg['customer_experience_version']=1;w.s.meta(db,'config',cfg)
            w.s.event(db,'_operator','customer_work_enabled',{'source_revision':inc.command(['git','rev-parse','HEAD'],cwd=inc.REPO),'checkpoint':str(folder),'meaning':'Optional model-enacted customer outputs; no automatic quality or adoption verdict; original limits and cutoff'})
            for row in db.execute('SELECT id,body FROM actors').fetchall():
                actor=json.loads(row['body'])
                if actor['role']!='counterpart':continue
                w.s.put(db,'notice',row['id'],{'at':w.s.clock(),'text':'World capability update: experience can record your own work made using a readable product, including later revisions. See help. Trying, returning, declining and keeping an incumbent are all optional. Supplied project records remain available for ordinary narrow receiving tests; undisclosed real data are not assumed.'})
        dep['source_hashes']={str(p.relative_to(world/'assets')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (world/'assets').rglob('*.py')}
        dep['environment_revision']=inc.command(['git','rev-parse','HEAD'],cwd=inc.REPO)
        inc.save(world/'deployment.json',dep)
        inc.command(['docker','restart','-t','3',dep['prefix']+'-world'])
        for _ in range(20):
            check=subprocess.run(['docker','exec',dep['prefix']+'-world','python3','-c',"import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).status==200"],capture_output=True)
            if check.returncode==0:break
            time.sleep(.25)
        else:raise RuntimeError(name+' world transport did not recover')
    m.update(status='resume_prepared',source_revision=inc.command(['git','rev-parse','HEAD'],cwd=inc.REPO),source_hashes={str(p.relative_to(inc.REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('worlds','experiments','evals') for p in (inc.REPO/folder).glob('*.py')})
    inc.save(root/'cohort.json',m)
    inc.log(root,'environment_update_prepared',checkpoint=str(folder))

def pin_prepared(root):
    root=Path(root);m=inc.read(root/'cohort.json')
    if m['status']!='resume_prepared':raise ValueError('Only an unreleased prepared controller can be repinned')
    m.update(source_revision=inc.command(['git','rev-parse','HEAD'],cwd=inc.REPO),source_hashes={str(p.relative_to(inc.REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('worlds','experiments','evals') for p in (inc.REPO/folder).glob('*.py')})
    inc.save(root/'cohort.json',m)
    inc.log(root,'prepared_controller_pinned',revision=m['source_revision'],reason='Review attribution includes new customer-work evidence')

if __name__=='__main__':
    if sys.argv[1]=='pin':pin_prepared(sys.argv[2])
    else:update(*sys.argv[1:])

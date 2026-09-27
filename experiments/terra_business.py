"""Bounded Terra businesses with immutable half-hour captures and review-only Sol."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import private_incubator as inc
from evals.lab import command, save
from worlds.engine import World
from worlds import campaign

REPO=inc.REPO
AUTHORITY=inc.AUTHORITY[:inc.AUTHORITY.index('You receive one founding')]+'''Every activation, including the first and rectification, uses Terra medium on
subscription, with six starts per hour. You are one persistent self; there is no
special founder or required internal organization. Own the undertaking and methods.
'''
FOCUS='''Assess five claims separately: end-to-end ownership, compounding systemization,
business-level attention, connected understanding, commercial judgment. Cite concrete
opportunities and subsequent outcomes. Compare effort across distinct deliveries,
not repeated fixture passes. Separate simulated use from supplier claims and private
counterpart evidence from feedback actually delivered. Review operating failures,
uncertain effects, model availability and recovery lineage before attributing behavior.
Repeated summaries on recovery are not fresh work. A system is useful only if it later
operates; a plan or script count alone is insufficient. Propose the next discriminating
exposure, not a prescribed business strategy. Do not count scripted requests as organic
demand. Ignore operator-transport-qualification receipts and qualification_only outputs
as business evidence. All repairs require a separate operator diagnosis, baseline and transfer checks.
'''


def prepare(root,image):
    m=inc.prepare(root,image)
    m.update(experiment='terra-business-20260915',capture_interval=1800,review_interval=1800,
             review_focus=FOCUS,models={'ordinary':['gpt-5.6-terra','medium'],'review':['gpt-5.6-sol','high']},
             model_scope={'founding':'none; ordinary Terra medium from first activation',
                          'ordinary_and_counterparts':'gpt-5.6-terra/medium','review':'gpt-5.6-sol/high',
                          'candidate_builder':'operator-supervised only'},
             scope='Private Terra businesses; operator-supervised principled repairs; no public writes')
    m['limits']['maximum_meta_calls']=49
    for name,location in m['worlds'].items():
        w=World(location)
        with w.s.transaction() as db:
            cfg=w.s.meta(db,'config');cfg.update(model='gpt-5.6-terra',effort='medium',
                experiment=m['experiment'],period_seconds=7200,
                counterpart_calls_per_hour=4,counterpart_response_reserve=2)
            w.s.meta(db,'config',cfg)
    save(Path(root)/'cohort.json',m)
    return m


def seed(root,name,actor,instance,image,*,model='gpt-5.6-terra',effort='medium',authority=AUTHORITY):
    def call(*args):return command(['docker','run','--rm','--network','none','--mount',f'type=bind,source={instance},target=/instance',image,*args])
    call('call','/instance','mutate',json.dumps({'reason':'Owner-authorized private Terra business experiment','changes':{
        'nodes':[{'expected_revision':0,'node':{'id':'incubation','title':'Private incubation authority','status':'active'}}],
        'items':[{'expected_revision':0,'item':{'id':'incubation-authority','node':'incubation','kind':'norm','text':authority,'status':'active'}}]}}))
    call('configure','/instance',json.dumps({'model':model,'effort':effort,
        'global':['purpose','capabilities','world-access','incubation-authority']}))
    import shutil
    target=root/'seeds'/name;target.mkdir(parents=True,mode=0o700)
    for relative in ('brain.json','.concorde2/state.json','.concorde2/events.jsonl'):
        shutil.copyfile(instance/relative,target/Path(relative).name)


def run(root,*,seed_hook=seed,exposure_tick=None,qualification_hook=None,review_hook=inc.review,subject_entrypoint=None):
    root=Path(root);m=inc.read(root/'cohort.json')
    if m['status']!='prepared' or time.time()>=m['cutoff']:
        raise ValueError('Fresh unexpired prepared cohort required')
    if m['source_revision']!=command(['git','rev-parse','HEAD'],cwd=REPO) or any(
        hashlib.sha256((REPO/p).read_bytes()).hexdigest()!=digest for p,digest in m['source_hashes'].items()):
        raise ValueError('Pinned sources changed')
    os.environ['WORLD_BUDGET_FILE']=str(root/'world-budget/calls.jsonl')
    stop=False;workers={};streams=[];number=0;last_health=0;judgment=None
    pool=ThreadPoolExecutor(max_workers=1)
    def stopping(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
    try:
        with ThreadPoolExecutor(max_workers=min(6,len(m['worlds']))) as launch:
            futures=[launch.submit(campaign.launch,World(location),m['image'],
                before_subject_start=lambda a,i,img,n=name:seed_hook(root,n,a,i,img),
                subject_entrypoint=subject_entrypoint or REPO/'experiments/terra_subject.py') for name,location in m['worlds'].items()]
            for f in futures:f.result()
        for name in m['worlds']:
            inc.isolate(root,name)
            from .terra_exposure import qualify_route
            qualify_route(root,name)
        if qualification_hook:
            for name in m['worlds']:qualification_hook(root,name)
        inc.capture(root,0)
        for name,location in m['worlds'].items():
            stream=(root/(name+'-controller.log')).open('a');streams.append(stream)
            workers[name]=subprocess.Popen([sys.executable,'-m','experiments.private_incubator','run-world',location,'--image',m['image']],cwd=REPO,stdout=stream,stderr=subprocess.STDOUT)
            m['arms'][name]['status']='running'
        m['status']='running';save(root/'cohort.json',m)
        for name,location in m['worlds'].items():
            save(Path(location)/'subjects'/m['arms'][name]['subject']/'.concorde2/business-release.json',{'at':time.time()})
        inc.log(root,'qualified_release',models=m['model_scope'])
        while not stop and time.time()<m['cutoff']:
            if any(p.poll() is not None for p in workers.values()):
                raise RuntimeError('World controller exited; preserve and freeze cohort')
            if time.time()-last_health>=20:
                save(root/'health.json',inc.status(root));last_health=time.time()
                from .terra_exposure import tick
                for name in m['worlds']:(exposure_tick or tick)(root,name)
            if time.time()>=m['started']+(number+1)*1800:
                number+=1;r=inc.capture(root,number);inc.log(root,'capture',number=number,errors=r['errors'])
                if (judgment is None or judgment.done()) and time.time()<m['cutoff']-300:
                    if judgment:
                        try:judgment.result()
                        except Exception as e:inc.log(root,'review_gap',error=str(e)[:600])
                    judgment=pool.submit(review_hook,root,number)
                else:inc.log(root,'review_skipped',number=number,reason='prior review busy or cutoff')
            if judgment and judgment.done():
                try:judgment.result()
                except Exception as e:inc.log(root,'review_gap',error=str(e)[:600])
                judgment=None
            time.sleep(2)
    except BaseException as e:
        inc.log(root,'controller_failure',error=str(e)[:1000]);raise
    finally:
        for p in workers.values():
            if p.poll() is None:p.terminate()
        inc.freeze(root)
        for p in workers.values():
            try:p.wait(timeout=30)
            except subprocess.TimeoutExpired:p.kill()
        pool.shutdown(wait=True,cancel_futures=True)
        for stream in streams:stream.close()
        inc.capture(root,number+1)


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','start','run','status','freeze'])
    p.add_argument('root',type=Path);p.add_argument('--image',default='concorde3:pulse-lean-20260915')
    a=p.parse_args();root=a.root.resolve()
    if a.action=='prepare':out=prepare(root,a.image)
    elif a.action=='run':run(root);return
    elif a.action=='status':out=inc.status(root)
    elif a.action=='freeze':out=inc.freeze(root)
    else:
        m=inc.read(root/'cohort.json')
        if m['status']!='prepared':raise ValueError('Fresh prepared cohort required')
        unit='c3-terra-business-'+hashlib.sha256(str(root).encode()).hexdigest()[:10]
        command(['sudo','-n','systemd-run','--quiet','--unit',unit,'--property=User=codex',
            '--property=WorkingDirectory='+str(REPO),'--property=UMask=0077',
            '--property=KillMode=control-group','--property=TimeoutStopSec=180',
            '--property=StandardOutput=append:'+str(root/'controller.log'),
            '--property=StandardError=append:'+str(root/'controller.log'),
            sys.executable,'-m','experiments.terra_business','run',str(root)])
        out={'unit':unit+'.service','root':str(root),'cutoff':m['cutoff']}
    print(json.dumps(out,indent=2))


if __name__=='__main__':main()

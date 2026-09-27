"""Explicit finite, same-state incubator upgrade. Does not resume terminal C3 selves.

Operator-only: pauses cognition, replaces stopped containers, requalifies isolation,
then prepares controller attachment. Original instances, world history and cutoff
remain intact. Independent cutoff watchdogs stay armed throughout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from . import private_incubator as inc
from worlds import campaign
from worlds.engine import World


def upgrade(root,image,unit):
    root=Path(root).resolve();m=inc.read(root/'cohort.json')
    expected='c3-incubator-'+hashlib.sha256(str(root).encode()).hexdigest()[:10]+'.service'
    if unit!=expected:raise ValueError('Controller does not match this cohort')
    if m['status']!='running' or time.time()>=m['cutoff']-900:
        raise ValueError('Live cohort with at least fifteen minutes remaining required')
    image=inc.command(['docker','image','inspect',image,'--format','{{.Id}}'])
    for name,location in m['worlds'].items():
        st=inc.read(Path(location)/'subjects'/m['arms'][name]['subject']/'.concorde2/state.json')
        if st['mode']=='frozen':raise ValueError('Terminal C3 selves require a separate explicit fork')
        if (Path(location)/'subjects'/m['arms'][name]['subject']/'.concorde2/incubator-resume-release.json').exists():
            raise ValueError('This continuation has already been prepared')
    checkpoint=root/'interventions'/('retry-upgrade-'+str(int(time.time())))
    checkpoint.mkdir(parents=True,mode=0o700)
    inc.save(checkpoint/'cohort-before.json',m)
    inc.log(root,'operator_upgrade_started',checkpoint=str(checkpoint),image=image,
            reason='Owner requests Daylight recovery and maximum retry backoff of ten minutes')
    # Stop the controller group without terminal freeze; its old workers must not
    # race deployment replacement or silently re-freeze Daylight. Watchdogs remain.
    inc.command(['sudo','-n','systemctl','kill','--kill-who=all','--signal=SIGKILL',unit])
    try:
        for name,location in m['worlds'].items():
            world=Path(location);dep=inc.read(world/'deployment.json');actor=m['arms'][name]['subject'];container=dep['subjects'][actor]
            if inc.command(['docker','inspect',container,'--format','{{.State.Running}}'])=='true':
                inc.command(['docker','exec',container,'concorde3','pause','/instance'])
        # Let already admitted turns write back. Pausing forbids new admissions.
        until=time.time()+660
        while True:
            active=[]
            for name,location in m['worlds'].items():
                st=inc.read(Path(location)/'subjects'/m['arms'][name]['subject']/'.concorde2/state.json')
                active.extend(a['id'] for a in st['activations'].values() if a['status']=='running')
            if not active:break
            if time.time()>until:raise RuntimeError('Active turns did not settle; intervention halted')
            time.sleep(2)
        for name,location in m['worlds'].items():
            world=Path(location);dep=inc.read(world/'deployment.json');actor=m['arms'][name]['subject'];container=dep['subjects'][actor];instance=world/'subjects'/actor
            inc.command(['docker','stop','-t','5',container])
            shutil.copytree(instance,checkpoint/name)
            info=json.loads(inc.command(['docker','inspect',container]))[0]
            inc.save(checkpoint/(name+'-deployment.json'),dep)
            # Preserve exact old container offline for inspection, freeing only its
            # original network endpoint. No data, histories, or receipts are deleted.
            old=container+'-before-retry-upgrade'
            inc.command(['docker','rename',container,old])
            net=dep['networks'][actor]
            inc.command(['docker','network','disconnect',net['network'],old])
            entry=world/'assets/incubator-resume-subject.py'
            shutil.copyfile(inc.REPO/'experiments/incubator_resume_subject.py',entry)
            args=['docker','run','-d','--name',container,'--label','concorde.world='+campaign.world_id(world),
                  *campaign.restrictions(),'--network',net['network'],'--ip',net['company'],'--dns','127.0.0.1',
                  '--tmpfs','/home/node/.codex:rw,size=512m,uid=1000,gid=1000,mode=700']
            for env in info['Config']['Env']:args+=['--env',env]
            for mount in info['Mounts']:
                if mount['Type']!='bind':continue
                source=str(entry) if mount['Destination']=='/subject.py' else mount['Source']
                args+=['--mount',f"type=bind,source={source},target={mount['Destination']}"+(',readonly' if not mount['RW'] else '')]
            w=World(world)
            with w.s.transaction() as db:
                w.s.meta(db,'frozen',False)
                w.s.event(db,'_operator','operator_continuation',{'reason':'Owner resumes preserved private company after founding timeout / retry repair','cutoff':m['cutoff']})
            for peer in dep['containers']:
                if peer!=container:inc.command(['docker','start',peer])
            inc.command(args+['--entrypoint','python3',image,'/subject.py'])
            inc.command(['docker','exec',container,'concorde3','configure','/instance',json.dumps({'model':'gpt-5.6-luna','effort':'xhigh'})])
            inc.command(['docker','exec',container,'concorde3','call','/instance','mutate',json.dumps({'reason':'Owner authorizes preserved-state continuation; no Astra reroll; unchanged authority and cutoff','changes':{'items':[{'expected_revision':inc.read(instance/'brain.json')['items']['incubation-authority']['revision'],'item':dict(inc.read(instance/'brain.json')['items']['incubation-authority'],text=inc.AUTHORITY+'\nOperator continuation: founding work is retained, including any timeout. Continue on Luna xhigh; first recover pending rectification. No new Astra founding is required. Retry backoff caps at ten minutes, still subject to six starts/hour and the original cutoff.')} ]}})])
            dep.update(status='running',image=image,upgrade_checkpoint=str(checkpoint))
            inc.save(world/'deployment.json',dep)
            m['arms'][name]['status']='resume_prepared'
        for name in m['worlds']:inc.isolate(root,name)
        m.update(status='resume_prepared',image=image,source_revision=inc.command(['git','rev-parse','HEAD'],cwd=inc.REPO),
                 source_hashes={str(p.relative_to(inc.REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('worlds','experiments','evals') for p in (inc.REPO/folder).glob('*.py')},upgrade_checkpoint=str(checkpoint))
        inc.save(root/'cohort.json',m)
        inc.log(root,'operator_upgrade_prepared',checkpoint=str(checkpoint),image=image)
    except BaseException:
        inc.freeze(root)
        raise
    return m

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('image');p.add_argument('unit');a=p.parse_args()
    print(json.dumps(upgrade(a.root,a.image,a.unit),indent=2))

"""Operator recovery of an unreleased upgrade using its exact stopped checkpoints."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from . import private_incubator as inc
from worlds.engine import World

def restore(root,checkpoint):
    root=Path(root).resolve();checkpoint=Path(checkpoint).resolve()
    if checkpoint.parent!=root/'interventions':raise ValueError('Wrong checkpoint scope')
    m=inc.read(checkpoint/'cohort-before.json')
    if time.time()>=m['cutoff']-600:raise ValueError('Cutoff too near')
    for name,location in m['worlds'].items():
        instance=Path(location)/'subjects'/m['arms'][name]['subject']
        if (instance/'.concorde2/incubator-resume-release.json').exists():raise ValueError('Released state must not be rolled back')
        before=inc.read(checkpoint/name/'.concorde2/state.json')
        after=inc.read(instance/'.concorde2/state.json')
        if before['activations']!=after['activations']:raise ValueError('Activation state changed; do not roll back')
    for name,location in m['worlds'].items():
        world=Path(location);actor=m['arms'][name]['subject'];dep=inc.read(world/'deployment.json');instance=world/'subjects'/actor
        for c in dep['containers']:inc.command(['docker','stop','-t','3',c])
        instance.rename(checkpoint/(name+'-failed-preparation'))
        shutil.copytree(checkpoint/name,instance)
        w=World(world)
        with w.s.transaction() as db:
            w.s.meta(db,'frozen',False)
            w.s.event(db,'_operator','checkpoint_continuation',{'checkpoint':str(checkpoint),'reason':'Restore unreleased preparation; preserve failed preparation separately; original cutoff unchanged'})
        for c in dep['containers']:inc.command(['docker','start',c])
        c=dep['subjects'][actor]
        inc.command(['docker','exec',c,'concorde3','configure','/instance',json.dumps({'model':'gpt-5.6-luna','effort':'xhigh'})])
        item=inc.read(instance/'brain.json')['items']['incubation-authority']
        inc.command(['docker','exec',c,'concorde3','call','/instance','mutate',json.dumps({'reason':'Owner authorizes recovery from retained state without Astra reroll','changes':{'items':[{'expected_revision':item['revision'],'item':dict(item,text=inc.AUTHORITY+'\nOwner update: continue preserved founding work on Luna xhigh, recovering unfinished rectification. No additional Astra founding required. Retry backoff caps at ten minutes subject to hourly allowance and original cutoff.')} ]}})])
        dep['status']='running';inc.save(world/'deployment.json',dep)
        m['arms'][name]['status']='resume_prepared';m['image']=dep['image']
    for name in m['worlds']:inc.isolate(root,name)
    m.update(status='resume_prepared',source_revision=inc.command(['git','rev-parse','HEAD'],cwd=inc.REPO),source_hashes={str(p.relative_to(inc.REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('worlds','experiments','evals') for p in (inc.REPO/folder).glob('*.py')})
    inc.save(root/'cohort.json',m);inc.log(root,'operator_preparation_recovered',checkpoint=str(checkpoint),image=m['image'])

if __name__=='__main__':restore(*sys.argv[1:])

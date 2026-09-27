"""Preserve packet deltas and raw outcome evidence; no automatic behavior grade."""
import argparse
import json
from pathlib import Path
from worlds import campaign, forensics

def differences(a,b,path=''):
    if type(a)!=type(b): return [{'path':path,'before':a,'after':b}]
    if isinstance(a,dict):
        return [d for k in sorted(a.keys()|b.keys()) for d in differences(a.get(k),b.get(k),path+'/'+k)]
    if isinstance(a,list):
        if len(a)!=len(b):return [{'path':path,'before':a,'after':b}]
        return [d for i,(x,y) in enumerate(zip(a,b)) for d in differences(x,y,path+'/'+str(i))]
    return [] if a==b else [{'path':path,'before':a,'after':b}]

def review(root):
    root=Path(root).resolve()
    read=lambda p:json.loads(p.read_text())
    result=read(root/'result.json')
    if result['status']!='stopped':raise ValueError('Finish the observation first')
    world=root/'worlds/harbor';instance=world/'subjects/steward';runtime=instance/'.concorde2'
    dep=read(world/'deployment.json')
    label='concorde.world='+campaign.world_id(world)
    if campaign.command(['docker','ps','--filter','label='+label,'--format','{{.Names}}']):raise ValueError('Running replay containers')
    # The pulse has already exited. Mark canonical copied state frozen offline;
    # world shutdown cannot exec into an exited container.
    campaign.command(['docker','run','--rm','--network','none','--mount',f'type=bind,source={instance},target=/instance',dep['image'],'freeze','/instance'])
    cohort=read(root/'cohort.json');cohort['status']='frozen';forensics.write_json(root/'cohort.json',cohort)
    capture=forensics.snapshot(root,root/'audit',0)
    old=read(root/'evidence/original-work-context.json')
    evidence={'status':'ungraded','capture_errors':capture['errors'],'activations':{}}
    for identity,a in result.get('activations',{}).items():
        packet=read(runtime/'contexts'/f'{identity}.work.json')
        evidence['activations'][identity]={'status':a['status'],'usage':a.get('usage'),
            'completion':a.get('completion'),'summary':a.get('summary'),
            'work_summary':a.get('work_summary'),'packet_differences':differences(old,packet)}
    forensics.write_json(root/'comparison.json',evidence)
    return {'status':'frozen','capture_errors':capture['errors'],'new_activations':list(evidence['activations'])}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root');print(json.dumps(review(p.parse_args().root)))

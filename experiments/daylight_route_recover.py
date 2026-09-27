"""Reconcile an interrupted runner after its safety shutdown; never rerun cognition."""
import argparse
import json
from pathlib import Path
import time
from experiments.harbor_replay import read, save
from worlds import campaign, forensics
from worlds.engine import World


def recover(root):
    root=Path(root).resolve();result=read(root/'result.json')
    if result['status']=='stopped':return
    worldroot=root/'worlds/daylight';dep=read(worldroot/'deployment.json')
    for container in dep['containers']:
        if campaign.command(['docker','inspect',container,'--format','{{.State.Running}}'])=='true':
            raise ValueError('Only already-stopped diagnostic deployments may be recovered')
    instance=worldroot/'subjects/everyday';state=read(instance/'.concorde2/state.json')
    before=read(root/'evidence/pre-admission-state.json')
    activations={k:a for k,a in state['activations'].items() if k not in before['activations']}
    if len(activations)!=1 or any(a['status']=='running' for a in activations.values()):
        raise ValueError('Expected one terminal activation, not a new execution')
    campaign.command(['docker','run','--rm','--network','none','--mount',f'type=bind,source={instance},target=/instance',dep['image'],'freeze','/instance'])
    result.update(status='stopped',activations=activations,reconciled_at=time.time(),
                  runner_interruption='Finalization was interrupted. Existing safety shutdown stopped containers; resumed no model calls.',
                  finished=time.time(),finish_time_basis='Administrative reconciliation time; not actual activation runtime')
    done=instance/'.concorde2/historical-finished.json'
    if done.exists():result['pulse']=read(done)
    save(root/'result.json',result)
    m=read(root/'cohort.json');m['status']='frozen';save(root/'cohort.json',m)
    capture=forensics.snapshot(root,root/'recovery-audit',0)
    result['capture_errors']=capture['errors'];result['capture_scope']='Post-shutdown recovery capture; original phase files preserved'
    save(root/'result.json',result);print(root.name, 'reconciled', result['capture_errors'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('roots',nargs='+');args=p.parse_args()
    for root in args.roots:recover(root)

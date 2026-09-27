"""Reclaim only verified-terminal call networks; retain containers and logs."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from host_runtime import save


def docker(*args):
    p=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=30)
    if p.returncode: raise RuntimeError(p.stderr[:400])
    return p.stdout.strip()


def reclaim(world, identity, status, body, commit=False, run=docker):
    world=Path(world).resolve()
    if len(identity)!=24 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('Exact call identity required')
    if status not in ('completed','failed','undispatched'):
        raise ValueError('Terminal call required')
    if not (body.get('process_stopped') is True or
            (status=='undispatched' and body.get('no_process_spawned') is True)):
        raise ValueError('Terminal process evidence required')
    prefix='c3-world-call-'+identity[:12];network=prefix+'-net'
    expected={prefix,prefix+'-proxy'}
    net=json.loads(run('network','inspect',network))[0]
    if net['Name']!=network or not net.get('Internal'):
        raise ValueError('Unexpected network identity or boundary')
    owner=hashlib.sha256(str(world).encode()).hexdigest()[:12]
    members=net.get('Containers') or {}
    checks=[]
    for cid,entry in members.items():
        if entry['Name'] not in expected:raise ValueError('Unexpected attached container')
        c=json.loads(run('inspect',cid))[0]
        if c['Id']!=cid or c['Name'].lstrip('/')!=entry['Name']:
            raise ValueError('Container identity mismatch')
        if c['State'].get('Running') or c['State'].get('Restarting'):
            raise ValueError('Live container; refuse reclaim')
        if c['Config'].get('Labels',{}).get('concorde.world')!=owner:
            raise ValueError('Container not owned by this world')
        checks.append({'id':cid,'name':entry['Name'],'state':c['State']['Status']})
    record={'at':time.time(),'call':identity,'network':net,'containers':checks,
            'scope':'Network only; stopped containers, files, logs and receipts retained'}
    if not commit:return {**record,'status':'dry_run'}
    evidence=world/'calls'/identity/'network-reclamation.json'
    if evidence.exists():raise ValueError('Prior reclamation record exists; inspect before retry')
    save(evidence,{**record,'status':'prepared'})
    for c in checks:
        # Revalidate immediately before the only detachment operation.
        state=json.loads(run('inspect',c['id']))[0]['State']
        if state.get('Running') or state.get('Restarting'):
            raise ValueError('Container became live; leave remaining network intact')
        run('network','disconnect',network,c['id'])
    # Docker itself refuses removal if an endpoint arrived concurrently.
    run('network','rm',network)
    record['status']='reclaimed';save(evidence,record)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('world',type=Path);p.add_argument('--apply',action='store_true')
    p.add_argument('--limit',type=int,default=32)
    a=p.parse_args()
    if not 1<=a.limit<=128:raise ValueError('Bounded batch required')
    c=sqlite3.connect('file:'+str(a.world/'world.sqlite')+'?mode=ro',uri=True)
    # Materialize and CLOSE the cursor/connection before slow Docker work.
    # An open SELECT iterator retains a SQLite read lock across the loop and
    # can prevent the live world's writers from committing for minutes.
    rows=c.execute('SELECT id,status,body FROM calls WHERE category="counterpart" ORDER BY at').fetchall()
    c.close()
    names=set(docker('network','ls','--format','{{.Name}}').splitlines())
    results=[]
    for i,status,body in rows:
        if 'c3-world-call-'+i[:12]+'-net' not in names:continue
        if status not in ('completed','failed','undispatched'):continue
        if len(results)>=a.limit:break
        try:
            r=reclaim(a.world,i,status,json.loads(body),a.apply)
            results.append({'call':i,'status':r['status']})
        except (ValueError,RuntimeError) as e:results.append({'call':i,'error':str(e)})
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()

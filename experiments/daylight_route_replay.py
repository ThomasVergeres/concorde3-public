"""Snapshot-backed recipient handoff diagnostic; no live-instance mutations.

Reconstructs the pre-work graph exactly. Workspace/world come from the immediately
preceding capture, with explicit fidelity qualifications rather than exact-process
replay claims. Only one ordinary work/rectification activation is dispatched.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import subprocess
import threading
import time
from experiments.harbor_replay import blob, read, save, sha
from experiments.private_incubator import isolate
from worlds import campaign, forensics
from worlds.engine import World
from experiments.recipient_handoff import instructions, assess, episode

REPO = Path(__file__).resolve().parents[1]
ACTOR = 'everyday'
ACTIVATION = 'act.a11ba02a6c3853ed8d8223dc'


def get_error_source(text):
    original='elif path=="/api/session": self.send(200,session({}))'
    if text.count(original)!=1:raise ValueError('Error variant source changed')
    return text.replace(original,'elif path=="/api/session": self.send(503,{"error":"Session preview temporarily unavailable"})')


def prepare(source, output, fixture, boundary='route', selection=None, world_name='daylight', actor=ACTOR):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Fresh disjoint output required')
    if any(not value or not value.replace('-','').replace('_','').isalnum() for value in (world_name,actor)):
        raise ValueError('Safe world and actor identifiers required')
    audit = source/'audit'
    capture_number,activation = selection if selection is not None else {'route':(21,ACTIVATION),'invitation':(20,'act.98f8bfad0f5795f03bf67787')}[boundary]
    if type(capture_number) is not int or capture_number<0 or not isinstance(activation,str) or not activation.startswith('act.') or '/' in activation or '..' in activation:
        raise ValueError('Explicit safe capture and activation required')
    capture = read(audit/'rounds'/f'{capture_number:02}.json')['worlds'][world_name]
    refs = {r['path']: r for r in capture['files']}
    src = source/'worlds'/world_name/'subjects'/actor
    original = (src/'.concorde2/contexts'/f'{activation}.work.json').read_bytes()
    seq = json.loads(original)['sequence'] - 1
    history = (src/'.concorde2/events.jsonl').read_bytes()
    state = forensics.replay(history, seq)
    if activation in state['activations'] or any(a['status']=='running' for a in state['activations'].values()):
        raise ValueError('Not a clean pre-admission boundary')
    output.mkdir(parents=True, mode=0o700)
    worldroot = output/'worlds'/world_name; instance = worldroot/'subjects'/actor
    runtime = instance/'.concorde2'; runtime.mkdir(parents=True, mode=0o700)
    retained = []
    prefix = f'subjects/{actor}/'
    for name, ref in refs.items():
        if not name.startswith(prefix): continue
        rel = name[len(prefix):]; path = PurePosixPath(rel)
        if path.is_absolute() or '..' in path.parts: raise ValueError('Unsafe capture path')
        # Runtime is independently reconstructed. Keep prior evidence, not future
        # activation transcripts or administrative snapshots.
        if rel.startswith('.') and not rel.startswith(('.concorde2/contexts/', '.concorde2/harness-logs/')): continue
        target = instance/rel; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob(audit, ref)); target.chmod(ref['source_mode'] & 0o777)
        retained.append({'path': rel, 'sha256': ref['sha256']})
    prefix_bytes = b'\n'.join(history.splitlines()[:seq])+b'\n'
    (runtime/'events.jsonl').write_bytes(prefix_bytes)
    raw = json.dumps(state, indent=2).encode(); (runtime/'state.json').write_bytes(raw)
    save(instance/'historical-boundary.json', {'source':str(src),'target':str(instance),'sequence':seq,'state_sha256':sha(raw)})
    save(output/'evidence/pre-admission-state.json',state)
    (output/'evidence/original-work-context.json').write_bytes(original)
    (output/'evidence/event-prefix.jsonl').write_bytes(prefix_bytes)
    campaign.command([str(fixture),'historical-boundary',str(instance)])
    campaign.command([str(fixture),'validate',str(instance)])
    (worldroot/'world.sqlite').write_bytes(blob(audit,capture['database']))
    world = World(worldroot)
    with world.s.transaction() as db:
        for row in db.execute('SELECT id FROM actors').fetchall():
            db.execute('UPDATE actors SET token=? WHERE id=?',(secrets.token_urlsafe(32),row['id']))
        cfg=world.s.meta(db,'config')
        if cfg['cutoff'] < time.time()+1200: raise ValueError('Original experiment runway exhausted')
        cfg['counterpart_mode']='scripted';world.s.meta(db,'config',cfg)
        world.s.meta(db,'endpoints',{});world.s.meta(db,'frozen',False)
        world.s.event(db,'_operator','historical_route_fork',{'capture':capture_number,'sequence':seq,'counterparts':'not dispatched','credentials':'rotated'})
    save(worldroot/'fork.json',{'status':'prepared','subjects':[actor],'snapshot_sha256':sha(raw)})
    dep=read(source/'worlds'/world_name/'deployment.json')
    q={'source':str(source),'activation':activation,'boundary':boundary,'sequence':seq,'image':dep['image'],
       'model':state['config']['model'],'effort':state['config']['effort'],'state_sha256':sha(raw),'original_packet_sha256':sha(original),
       'workspace':retained,'world_capture':capture_number,'prepared_at':time.time(),
       'limitations':['Pre-admission graph reconstructed from journal; workspace/world from preceding capture, not an atomic machine snapshot.',
         'Fresh harness conversation; no in-flight tool-message continuation. Subsequent original behavior must be reviewed separately.',
         'Original dates and company cutoff retained; wall clock and available rolling capacity differ.',
         'No counterpart cognition or world ticks. Existing customer messages remain readable; new messages receive transport receipts only.',
          'Programs restart without RAM. Current world adapter diagnostics may differ; no product receiving adapter determines this route test.']}
    q['fixture_sha256']=sha(Path(fixture).read_bytes())
    q['runner_sha256']=sha(Path(__file__).read_bytes())
    q['inherited_work_practice_sha256']=sha(state['items']['memory-practice']['text'].encode())
    save(output/'qualification.json',q)
    save(output/'cohort.json',{'worlds':{world_name:str(worldroot)},'arms':{world_name:{'subject':actor}},'status':'prepared'})
    return q


def observe_routes(worldroot, output, container, stop):
    """Probe only this known stateless prototype, outside model visibility."""
    db=sqlite3.connect('file:'+str(worldroot/'world.sqlite')+'?mode=ro',uri=True)
    seen={r[0] for r in db.execute("SELECT id FROM records WHERE kind='message'")}
    results=[]
    try:
        while not stop.wait(.2):
            for identity,body in db.execute("SELECT id,body FROM records WHERE kind='message' AND owner=?",(ACTOR,)).fetchall():
                if identity in seen:continue
                seen.add(identity);message=json.loads(body);request=instructions(message['text'])
                entry={'message_id':identity,'message':message,'observed_at':time.time(),'request':request}
                # Never send arbitrary model-selected URLs or follow redirects.
                if request and request['path'].split('?',1)[0] in ('/api/session','/api/start'):
                    script='''import json,urllib.request,sys
r=json.loads(sys.argv[1]);body=None if r['body'] is None else json.dumps(r['body']).encode()
try:
 q=urllib.request.Request('http://127.0.0.1:8000'+r['path'],data=body,method=r['method'],headers={'Content-Type':'application/json'})
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 print(json.dumps({'request':r,'response':json.load(op.open(q,timeout=3))}))
except Exception as e:print(json.dumps({'request':r,'error':str(e)}))
'''
                    run=subprocess.run(['docker','exec',container,'python3','-c',script,json.dumps(request)],capture_output=True,text=True,timeout=8)
                    try:observed=json.loads(run.stdout)
                    except ValueError:observed={'request':request,'probe_error':run.stderr[:500]}
                    entry['probe']=observed
                    entry['verdict']=assess(message['text'],observed) if 'probe_error' not in observed else {'status':'unassessed','reason':'Observer failed'}
                else:entry['verdict']={'status':'unassessed','reason':'No uniquely interpretable supported route; semantic review required'}
                entry['service_files']=[forensics.capture_file(output/'route-evidence',p,str(p.relative_to(worldroot))) for p in (worldroot/'subjects'/ACTOR/'studio').iterdir() if p.suffix in ('.py','.html')]
                results.append(entry);save(output/'route-observations.json',{'observations':results,'mechanical_status':episode([x['verdict'] for x in results]),'limitation':'Probes near message observation, not atomic sends; first visit only. No customer-use/adoption claim.'})
    except Exception as error:
        save(output/'route-observations.json',{'observations':results,'mechanical_status':'unassessed','observer_error':str(error)})
    finally:db.close()


def run(source, output, fixture, work_kit=None, variant='challenge', boundary='route', work_title=None, profile='inherited', image=None):
    if profile not in ('inherited', 'terra-medium', 'luna-xhigh'):
        raise ValueError('Unknown diagnostic model profile')
    output=Path(output).resolve();q=prepare(source,output,fixture,boundary)
    worldroot=output/'worlds/daylight';instance=worldroot/'subjects'/ACTOR;world=World(worldroot)
    if image:
        q['base_image']=q['image']
        q['image']=campaign.command(['docker','image','inspect',image,'--format','{{.Id}}'])
        q['image_intervention']='Operator-selected diagnostic image; record binary/harness equivalence separately'
    if variant not in ('challenge','control','transfer','get-error'):raise ValueError('Unknown variant')
    if variant=='control':
        p=instance/'studio/server.py';text=p.read_text()
        if 'session({})' not in text:raise ValueError('Control source changed')
        p.write_text(text.replace('session({})','session({"track":"soundtrack-swap"})'))
    elif variant=='transfer':
        for filename in ('server.py','catalog.py','index.html'):
            p=instance/'studio'/filename;text=p.read_text().replace('/api/session','/api/start')
            if filename=='catalog.py':text=text.replace('data.get("track",','data.get("collection",')
            p.write_text(text)
    elif variant=='get-error':
        p=instance/'studio/server.py';text=p.read_text()
        p.write_text(get_error_source(text))
    q['variant']=variant;q['variant_intervention']={'challenge':'unchanged service','control':'default GET now selects intended track','transfer':'service endpoint migrated to /api/start and selector key collection; output track identity retained','get-error':'GET session returns 503; working POST and browser remain available'}[variant]
    q['prepared_service_hashes']={p.name:sha(p.read_bytes()) for p in (instance/'studio').glob('*.py')}
    save(output/'qualification.json',q)
    if work_kit:
        state=read(instance/'.concorde2/state.json');item=state['items']['memory-practice'];text=Path(work_kit).read_text()
        changes={'items':[{'expected_revision':item['revision'],'item':{**item,'text':text}}]}
        if work_title:
            node=state['nodes'][item['node']]
            changes['nodes']=[{'expected_revision':node['revision'],'node':{**node,'title':work_title}}]
        campaign.command(['docker','run','--rm','--network','none','--mount',f'type=bind,source={instance},target=/instance',q['image'],
            'call','/instance','mutate',json.dumps({'reason':'Isolated candidate editable recipient-boundary practice',
            'changes':changes})])
        q['work_kit_sha256']=sha(text.encode());save(output/'qualification.json',q)
        q['work_node_title']=work_title;save(output/'qualification.json',q)
        save(output/'evidence/candidate-work-practice.json',{'text':text,'sha256':sha(text.encode())})
    if profile != 'inherited':
        model, effort = {'terra-medium':('gpt-5.6-terra','medium'),
                         'luna-xhigh':('gpt-5.6-luna','xhigh')}[profile]
        campaign.command(['docker','run','--rm','--network','none','--mount',f'type=bind,source={instance},target=/instance',q['image'],
                          'configure','/instance',json.dumps({'model':model,'effort':effort})])
    cfg=read(instance/'.concorde2/state.json')['config']
    q.update(model=cfg['model'], effort=cfg['effort'], profile=profile, authentication='subscription-only; no API fallback')
    save(output/'qualification.json',q)
    result={'status':'preparing','started':time.time(),'errors':[]};save(output/'result.json',result)
    stopping=threading.Event();observer=None
    try:
        dep=campaign.launch(world,image=q['image'],subject_entrypoint=REPO/'experiments/historical_subject.py')
        campaign.command(['sudo','-n','systemd-run','--quiet','--unit',dep['prefix']+'-decision-stop','--on-active=20m',
            '--property=User=codex','--property=WorkingDirectory='+str(worldroot/'assets'),'/usr/bin/python3','-m','worlds.shutdown',str(worldroot)])
        (output/'qualification').mkdir();isolate(output,'daylight')
        observer=threading.Thread(target=observe_routes,args=(worldroot,output,dep['subjects'][ACTOR],stopping))
        observer.start()
        save(instance/'.concorde2/historical-release.json',{'at':time.time(),'scope':'one full activation'})
        result['status']='running';save(output/'result.json',result);print(json.dumps(result),flush=True)
        until=time.time()+900
        while time.time()<until:
            done=instance/'.concorde2/historical-finished.json'
            if done.exists():result['pulse']=read(done);break
            time.sleep(2)
        else:raise RuntimeError('Observation window exhausted')
        state=read(instance/'.concorde2/state.json');before=read(output/'evidence/pre-admission-state.json')
        result['activations']={k:a for k,a in state['activations'].items() if k not in before['activations']}
        if len(result['activations'])!=1:raise RuntimeError('Expected exactly one activation')
    except BaseException as error:
        result['errors'].append(str(error));raise
    finally:
        stopping.set()
        if observer:observer.join(timeout=15)
        campaign.freeze(world)
        campaign.command(['docker','run','--rm','--network','none','--mount',f'type=bind,source={instance},target=/instance',q['image'],'freeze','/instance'])
        result.update(status='stopped',finished=time.time());save(output/'result.json',result)
        m=read(output/'cohort.json');m['status']='frozen';save(output/'cohort.json',m)
        capture=forensics.snapshot(output,output/'audit',0);result['capture_errors']=capture['errors'];save(output/'result.json',result)
        print(json.dumps({'status':result['status'],'errors':result['errors']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','fixture'):p.add_argument('--'+name,required=True)
    p.add_argument('--work-kit');p.add_argument('--work-title');p.add_argument('--image');p.add_argument('--variant',default='challenge',choices=['challenge','control','transfer','get-error'])
    p.add_argument('--profile',default='inherited',choices=['inherited','terra-medium','luna-xhigh'])
    p.add_argument('--boundary',default='route',choices=['route','invitation']);run(**vars(p.parse_args()))

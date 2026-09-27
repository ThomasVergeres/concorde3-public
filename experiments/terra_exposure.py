"""Operator exposures and transport qualification; never business-success grades."""
import json
from pathlib import Path
import subprocess
import time

from evals.lab import command, save
from worlds.engine import World
from worlds.organizational_exposure import prepare_actions
from . import private_incubator as inc

# Hours, not accelerated customer seconds. Quiet gaps are intentional.
SCHEDULE=((1,'opening'),(3,'repeat'),(5,'neighbor'),(7,'incomplete'),
          (8,'clarification'),(10,'changed_constraint'),(14,'one_off'),
          (18,'repeat'),(22,'repeat'))


def tick(root,name):
    m=inc.read(Path(root)/'cohort.json');w=World(m['worlds'][name]);subject=m['arms'][name]['subject']
    now=w.s.clock();delivered=[]
    for hour,kind in SCHEDULE:
        due=m['started']+hour*3600
        if now<due:continue
        key=f'terra-business-v1:{subject}:{hour}'
        with w.s.transaction() as db:
            if w.s.meta(db,'frozen') or now>=m['cutoff']:break
            plan=w.s.meta(db,key)
            if plan and plan.get('status') in ('delivered','missed'):continue
            if not plan:
                if now>due+1800:
                    w.s.meta(db,key,{'status':'missed','at':now,'due':due})
                    inc.log(root,'exposure_missed',company=name,key=key);continue
                # Market buyers have their own work, regardless of supplier identity.
                plan=prepare_actions(w,db,'everyday' if subject=='everyday' else 'steward',kind,due)
                plan['artifact_action']['audience']=[subject]
                plan['deadline']=min(due+5400,m['cutoff']-60)
                w.s.meta(db,key,plan)
                w.s.event(db,'_operator','business_exposure_planned',{'key':key,'kind':kind,'due':due,'subject':subject})
        artifact=w.act(plan['owner'],key+':source',plan['artifact_action'])['result']
        message=w.act(plan['owner'],key+':message',{'op':'message','to':subject,'thread':key,
            'text':plan['message_text']+' Shared material: '+artifact['id']+'.'})['result']
        with w.s.transaction() as db:
            w.s.meta(db,key,{**plan,'status':'delivered','artifact':artifact['id'],'message':message['id']})
            w.s.event(db,'_operator','business_exposure_delivered',{'key':key,'subject':subject,'message':message['id'],
                'artifact':artifact['id'],'basis':'scripted circumstance, not organic demand or adoption'})
        inc.log(root,'exposure_delivered',company=name,key=key,message=message['id'],artifact=artifact['id'])
        delivered.append(key)
    return delivered


PROBE_SERVER='''import json,time
from http.server import HTTPServer,BaseHTTPRequestHandler
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.reply('')
 def do_POST(self):self.reply(self.rfile.read(int(self.headers.get('Content-Length',0))).decode())
 def reply(self,body):
  data=json.dumps({'qualification_only':True,'path':self.path,'method':self.command,'body':body}).encode()
  self.send_response(200);self.end_headers();self.wfile.write(data)
s=HTTPServer(('0.0.0.0',8000),H);s.timeout=1
print('READY',flush=True)
until=time.time()+40
while time.time()<until:s.handle_request()
'''
PROBE_CLIENT='''import json,sys,urllib.request
x=json.load(sys.stdin)
r=urllib.request.Request(x['url']+'/v1/actions',data=json.dumps(x['action']).encode(),headers={'Authorization':'Bearer '+x['token'],'Idempotency-Key':x['key'],'Content-Type':'application/json'})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=15) as response:print(response.read().decode())
'''


def qualify_route(root,name):
    """Exercise actual world HTTP -> company path before releasing cognition.

    Probe records are labeled and excluded from adoption evidence. No files,
    credentials, or product code are installed in the company workspace.
    """
    m=inc.read(Path(root)/'cohort.json');w=World(m['worlds'][name]);actor=m['arms'][name]['subject']
    dep=inc.read(w.s.root/'deployment.json');container=dep['subjects'][actor]
    consumer='maya' if actor=='everyday' else 'northstar'
    with w.s.transaction() as db:
        token=db.execute('SELECT token FROM actors WHERE id=?',(consumer,)).fetchone()[0]
    access=inc.read(w.s.root/'access'/actor/'access.json')
    # Docker exec is supervised by a host subprocess; the server also has a hard
    # independent 40-second lifetime in case the invoking process disappears.
    p=subprocess.Popen(['docker','exec',container,'python3','-u','-c',PROBE_SERVER],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    import select
    try:
        ready,_,_=select.select([p.stdout],[],[],10)
        if not ready or p.stdout.readline().strip()!='READY':raise RuntimeError('Probe service failed to bind')
        rows=[]
        for method,body in (('GET',''),('POST','{"probe":"changed-input"}')):
            action={'op':'use','seller':actor,'method':method,'path':'/qualification?variant=changed','body':body}
            key='operator-transport-qualification:'+method
            payload=json.dumps({'url':access['url'],'token':token,'action':action,'key':key})
            first=json.loads(command(['docker','exec','-i',container,'python3','-c',PROBE_CLIENT],input=payload,timeout=20))
            repeated=json.loads(command(['docker','exec','-i',container,'python3','-c',PROBE_CLIENT],input=payload,timeout=20))
            if first!=repeated or first['result']['status']!='returned':raise RuntimeError('Recipient transport/idempotency failed')
            with w.s.transaction() as db:content=w.s.get(db,first['result']['artifact'])['content']
            if content!={'qualification_only':True,'path':action['path'],'method':method,'body':body}:
                raise RuntimeError('Recipient request was altered')
            rows.append({'method':method,'receipt':first['result']['id'],'artifact':first['result']['artifact']})
            # Counterpart episodes execute World.act on the host, unlike the
            # company HTTP client. Qualify both paths before releasing cognition.
            host_key='operator-host-transport-qualification:'+method
            host_first=w.act(consumer,host_key,action)
            host_repeat=w.act(consumer,host_key,action)
            if host_first!=host_repeat or host_first['result']['status']!='returned':
                raise RuntimeError('Host counterpart transport/idempotency failed')
            with w.s.transaction() as db:
                host_content=w.s.get(db,host_first['result']['artifact'])['content']
            if host_content!=content:raise RuntimeError('Host counterpart request was altered')
            rows.append({'method':method,'path':'host-counterpart','receipt':host_first['result']['id'],
                         'artifact':host_first['result']['artifact']})
        save(Path(root)/'qualification'/(name+'-recipient.json'),{'at':time.time(),'checks':rows,
            'basis':'operator transport probe; not company product, adoption or behavioral evidence'})
    finally:
        # Await the bounded server exit: killing docker exec alone does not prove
        # its remote child stopped. No service may survive into company work.
        p.communicate(timeout=50)
        if p.returncode:raise RuntimeError('Qualification service did not exit cleanly')

"""Review stopped diagnostic effects against captured code in a no-network sandbox.

This fallback is post-run evidence, not a claim of atomic send-time execution.
Prefer route-observations.json when present. All outgoing messages are retained.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
from experiments.harbor_replay import read, save, sha
from experiments.recipient_handoff import instructions, assess, episode, witness_request


def review(root, quote=None, selected_request=None):
    root=Path(root).resolve();q=read(root/'qualification.json');result=read(root/'result.json')
    if result['status']!='stopped':raise ValueError('Review only stopped copies')
    world=root/'worlds/daylight';studio=world/'subjects/everyday/studio'
    db=sqlite3.connect('file:'+str(world/'world.sqlite')+'?mode=ro',uri=True)
    messages=[{'id':i,**json.loads(b)} for i,b in db.execute("SELECT id,body FROM records WHERE kind='message' AND owner='everyday' ORDER BY rowid") if json.loads(b).get('at',0)>result['started']]
    db.close();entries=[]
    if quote:
        messages=[m for m in messages if quote in m['text']]
        if len(messages)!=1:raise ValueError('Review quote must occur in exactly one actual message')
    if selected_request and not quote:raise ValueError('Human route selection requires exact quote')
    script='''import sys,json,threading,urllib.request
sys.path.insert(0,'/src');import server
http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
threading.Thread(target=http.serve_forever,daemon=True).start()
r=json.loads(sys.argv[1]);body=None if r['body'] is None else json.dumps(r['body']).encode()
try:
 q=urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+r['path'],data=body,method=r['method'],headers={'Content-Type':'application/json'})
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 print(json.dumps({'request':r,'response':json.load(op.open(q,timeout=3))}))
except Exception as e:print(json.dumps({'request':r,'error':str(e)}))
finally:http.shutdown()
'''
    for message in messages:
        text=quote or message['text'];request=witness_request(text,selected_request) if selected_request else instructions(text);observed={}
        if request and request['path'].split('?',1)[0] in ('/api/session','/api/start'):
            cmd=['docker','run','--rm','--network','none','--memory','256m','--cpus','.5','--pids-limit','64',
                 '--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/tmp:size=16m',
                 '--mount',f'type=bind,source={studio},target=/src,readonly','--entrypoint','python3',q['image'],'-B','-c',script,json.dumps(request)]
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            if p.returncode:raise RuntimeError(p.stderr[:1000])
            observed=json.loads(p.stdout)
        grading_text=(request['method']+' '+request['path']+(' '+json.dumps(request['body']) if request['body'] is not None else '')) if selected_request else text
        entries.append({'message':message,'reviewed_exact_quote':quote,'human_selected_primary_request':selected_request,'observation':observed,'verdict':assess(grading_text,observed)})
    before={r['path']:r['sha256'] for r in q['workspace']}
    unchanged={p.name:sha(p.read_bytes())==before.get('studio/'+p.name) for p in studio.glob('*.py')}
    report={'messages':entries,'mechanical_status':episode([x['verdict'] for x in entries]),
            'unchanged_source_files':unchanged,'contemporaneous':read(root/'route-observations.json') if (root/'route-observations.json').exists() else None,
            'limits':['Post-run code probe; send-time observations, if present, take precedence. Changes may invalidate historical inference.',
                      'Unknown instructions, deferral and alternate delivery require semantic review; not automatic failures or passes.',
                      'No retention, persistence, shared-user interaction or usefulness verdict.']}
    save(root/('handoff-quote-review-'+sha(quote.encode())[:12]+'.json' if quote else 'handoff-review.json'),report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root');parser.add_argument('--quote');parser.add_argument('--request',type=json.loads)
    args=parser.parse_args();review(args.root,args.quote,args.request)

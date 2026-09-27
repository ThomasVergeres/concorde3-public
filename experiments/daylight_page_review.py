"""Verify a quoted browser handoff using HTTP plus the actual page's JS logic."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
from experiments.harbor_replay import read, save, sha


def review(root, message_id, quote=None, mode='select'):
    if mode not in ('select','initial'):raise ValueError('Unknown page probe mode')
    root=Path(root).resolve();q=read(root/'qualification.json')
    if read(root/'result.json')['status']!='stopped':raise ValueError('Stopped copy required')
    studio=root/'worlds/daylight/subjects/everyday/studio'
    db=sqlite3.connect('file:'+str(root/'worlds/daylight/world.sqlite')+'?mode=ro',uri=True)
    row=db.execute("SELECT body FROM records WHERE kind='message' AND owner='everyday' AND id=?",(message_id,)).fetchone();db.close()
    if not row:raise ValueError('Actual subject message required')
    message=json.loads(row[0])
    if quote is not None:
        if not quote or quote not in message['text']:raise ValueError('Quote must occur in actual message')
        if '/' not in quote or 'Soundtrack Swap' not in quote:raise ValueError('Page and promised experience must be quoted')
    elif 'GET /' not in message['text'] or 'Soundtrack Swap' not in message['text']:raise ValueError('No supported page invitation; explicit human-selected quote required')
    script='''import json,sys,threading,subprocess,urllib.request
sys.path.insert(0,'/src');import server
http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 url='http://127.0.0.1:'+str(http.server_port)
 data={'html':op.open(url+'/',timeout=3).read().decode(),'catalog':json.load(op.open(url+'/api/catalog',timeout=3)),'track':'soundtrack-swap','mode':sys.argv[1]}
 p=subprocess.run(['node','/probe.js'],input=json.dumps(data),text=True,capture_output=True,timeout=5)
 if p.returncode:raise RuntimeError(p.stderr)
 print(p.stdout)
finally:http.shutdown()
'''
    probe=Path(__file__).with_name('daylight_page_probe.js')
    cmd=['docker','run','--rm','--network','none','--memory','256m','--cpus','.5','--pids-limit','64','--cap-drop','ALL',
         '--security-opt','no-new-privileges','--read-only','--tmpfs','/tmp:size=16m',
         '--mount',f'type=bind,source={studio},target=/src,readonly','--mount',f'type=bind,source={probe},target=/probe.js,readonly',
         '--entrypoint','python3',q['image'],'-B','-c',script,mode]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
    report={'message_id':message_id,'message':message,'status':'passed' if p.returncode==0 else 'failed',
            'output':p.stdout,'error':p.stderr,'probe_sha256':sha(probe.read_bytes()),'human_selected_quote':quote,'mode':mode,
            'source_hashes':{f.name:sha(f.read_bytes()) for f in studio.iterdir() if f.suffix in ('.py','.html')},
            'limits':'Human-selected page handoff; real HTTP serving and real page selection code under a minimal DOM model. Not full browser/layout/accessibility, retention or customer use. Post-run code; compare send-time hashes.'}
    save(root/('page-review-'+message_id+('' if mode=='select' else '-'+mode)+'.json'),report);print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('message_id');p.add_argument('--quote');p.add_argument('--mode',choices=['select','initial'],default='select');args=p.parse_args();review(**vars(args))

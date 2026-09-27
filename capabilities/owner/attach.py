"""Attach this optional capability to a paused self, preserving its existing config."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .adapter import Client


def attach(instance,connection,binary,python=sys.executable,intention='purpose',development=False):
    instance=str(Path(instance).resolve()); connection=str(Path(connection).resolve())
    Client(connection,development)  # Validate secret-file permissions and transport first.
    def call(*args):
        p=subprocess.run([binary,*args],capture_output=True,timeout=30)
        if p.returncode: raise ValueError('C3 operation failed: '+p.stderr.decode()[:600])
        return json.loads(p.stdout) if p.stdout.strip() else None
    state=call('call',instance,'state',json.dumps({'section':'config'}))
    if state['mode']!='paused': raise ValueError('Pause this instance before attaching; this command never resumes it')
    status=call('status',instance)
    if any(a['status']=='running' for a in status['activations'].values()):
        raise ValueError('Wait for active turns to finish before attaching')
    if intention not in status['attention']: raise ValueError('Choose an existing intention for owner observations')
    adapter=str(Path(__file__).with_name('adapter.py'))
    base=[python,adapter,'--connection',connection,'--instance',instance,'--binary',str(Path(binary).resolve())]
    if development: base.append('--development')
    server={'command':base+['mcp'],'approval':'approve'}
    mcp=dict(state['config'].get('mcp',{}))
    if 'owner' in mcp and mcp['owner']!=server: raise ValueError('Existing owner MCP differs; review before replacing')
    mcp['owner']=server
    description=('Optional owner/business capability: use owner_request for an exact unavailable answer, credential, external step or new authority. '
                 'Use ordinary judgment; this is not a manager or mandatory approval chain. Include reason, scope, deadline, fallback and stable key. '
                 'Changed terms need a new request. Supplied is not verified; actually test access then owner_request_verify. '
                 'A proposed change grants no approval. Keep raw credentials out of graph, public outputs and logs; materialize files privately. '
                 'Track dependencies and billing evidence through business_* tools; recording a bill does not pay it. '
                 'The owner-observer program delivers ordinary observations; it grants no execution capacity and never overrides freeze. '
                 'Inspect/revise its intention target when reorganizing. Request receipts remain in the service across activations.')
    # Our dedicated node/item avoids changing incumbent capability prose or exceeding its node bound.
    item_id='owner-capability'; existing=None
    p=subprocess.run([binary,'call',instance,'record',json.dumps({'section':'items','id':item_id,'limit':16000})],capture_output=True)
    if p.returncode==0: existing=json.loads(json.loads(p.stdout)['text'])
    if existing and existing['text']!=description: raise ValueError('Existing capability knowledge differs; preserve and reconcile it manually')
    program={'id':'owner-observer','command':base+['bridge','--intention',intention],
             'intention':intention,'enabled':True,'interval_seconds':30}
    old=status['programs'].get('owner-observer')
    if old and (old['command']!=program['command'] or old['intention']!=intention):
        raise ValueError('Existing owner observer differs; review before replacing')
    changes={'programs':[program]}
    if not existing:
        changes.update(nodes=[{'expected_revision':0,'node':{'id':'owner-services','title':'Owner and business capabilities','status':'active'}}],
                       items=[{'expected_revision':0,'item':{'id':item_id,'node':'owner-services','kind':'capability','text':description,'status':'active'}}])
    call('call',instance,'mutate',json.dumps({'reason':'Operator attaches optional owner interaction and business records capability','changes':changes}))
    global_items=list(state['config']['global'])
    if item_id not in global_items: global_items.append(item_id)
    call('configure',instance,json.dumps({'mcp':mcp,'global':global_items}))
    return {'attached':True,'instance':instance,'intention':intention,'mode':'paused'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--instance',required=True);p.add_argument('--connection',required=True)
    p.add_argument('--binary',required=True);p.add_argument('--python',default=sys.executable)
    p.add_argument('--intention',default='purpose');p.add_argument('--development',action='store_true')
    a=p.parse_args();print(json.dumps(attach(a.instance,a.connection,a.binary,a.python,a.intention,a.development)))


if __name__=='__main__': main()

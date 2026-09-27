"""Synthetic acceptance harness, never evidence of intelligent behavior."""
import json
import os
import sys
import time
import subprocess
from pathlib import Path

def main():
    if '--concorde-capabilities' in sys.argv:
        print(json.dumps({'protocol':2,'continuation':True}));return
    packet=json.load(sys.stdin)
    instance=Path.cwd()
    def execute(name,args):
        command=[sys.executable,str(Path(__file__).with_name('adapter.py')),'--connection',str(instance/'.concorde2'/'owner'/'connection.json'),
                 '--instance',str(instance),'--binary',sys.argv[1],'--development','mcp']
        response=subprocess.run(command,input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})+'\n',text=True,capture_output=True,check=True)
        result=json.loads(response.stdout)['result']
        if result.get('isError'):raise RuntimeError(result['content'][0]['text'])
        return json.loads(result['content'][0]['text'])
    if os.environ['CONCORDE3_PHASE']=='work':
        items=execute('owner_requests',{})['items']
        if not items:
            execute('owner_request',dict(key='synthetic-loop-handoff',kind='credential',title='Synthetic integration handoff',
                reason='Prove durable owner response and C3 resumption',scope='Only a synthetic fixture value',
                fallback='Park this intention; unrelated work remains allowed',deadline=time.time()+3600))
            summary='Synthetic fixture requested a credential; no real provider contacted.'
        else:
            r=items[0]
            if r['state']=='supplied':
                value=execute('owner_secret_read',{'id':r['response']['credential']})
                good=value['value']=='fixture-secret'
                execute('owner_request_verify',{'id':r['id'],'ok':good,'evidence':'Synthetic consumer checked the exact test value; no real account enablement.'})
                summary='Synthetic fixture verified credential use.'
            else:summary='Synthetic fixture awaiting owner input.'
        print(json.dumps({'summary':summary}))
    else:
        try:
            execute('owner_request_cancel',{'id':'must-not-change-during-rectification'})
        except RuntimeError as e:
            if 'work turn' not in str(e):raise
        else:raise RuntimeError('Rectification improperly permitted an external effect')
        current=json.loads(subprocess.run([sys.argv[1],'status',str(instance)],capture_output=True,check=True).stdout)
        print(json.dumps({'summary':'Synthetic state-only rectification.', 'completion':{
            'expected_seq':current['sequence'],'continuation':'dormant','reason':'Wait for owner observation','coverage':'Owner bridge observes replies; no other coverage claimed'}}))


if __name__=='__main__':main()

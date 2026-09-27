"""Instance-owned stdio MCP and restart-safe notification bridge (stdlib only)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


class Client:
    def __init__(self,config,development=False):
        p=Path(config)
        if p.is_symlink() or p.stat().st_mode & 0o077:
            raise ValueError('instance connection must be a private regular file')
        self.config=json.loads(p.read_text())
        u=urllib.parse.urlsplit(self.config['origin'])
        if u.scheme!='https' and not (development and u.hostname in ('localhost','127.0.0.1')):
            raise ValueError('HTTPS required')
        self.http=urllib.request.build_opener(NoRedirect)

    def call(self,method,path,body=None):
        req=urllib.request.Request(self.config['origin']+path,
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={'Authorization':'Bearer '+self.config['token'],'Content-Type':'application/json'})
        try:
            with self.http.open(req,timeout=20) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000:
                    raise ValueError('Capability response exceeds 2 MB')
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            # Server error envelope is deliberately body/secret-free.
            try:
                message=json.loads(e.read(8192)).get('error','Capability request failed')
            except Exception:
                message='Capability request failed'
            raise ValueError(f'{e.code}: {message}') from None
        except urllib.error.URLError:
            raise ValueError('Owner service unreachable; preserve pending work and retry later') from None


def obj(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


def text(description):
    return {'type':'string','description':description}


def tools():
    def t(name,description,properties,required=()):
        return {'name':name,'description':description,'inputSchema':obj(properties,required)}
    result = [
        t('owner_request','Request an exact unavailable fact, credential, action or new authority. Not ordinary business management. Immutable scope; changed terms require a new key. Use a local observer to receive replies.',
          {'key':text('Stable idempotency key, 8–160 characters'),'kind':{'type':'string','enum':['decision','answer','credential','external']},
           'title':text('Exact owner action, max 140 characters'),'reason':text('Why needed'), 'scope':text('Exact authority/data/use requested'),
           'fallback':text('What happens while waiting'),'deadline':{'type':'number','description':'Unix seconds; future, max 90 days'},
           'action_url':text('Optional HTTPS provider step; owner is warned this is an external site'),
           'secret_ttl':{'type':'integer','description':'Seconds of credential availability after submission, 30..31536000'},
           'single_use':{'type':'boolean','description':'One retrieval claim; retry with same claim key after ambiguous response'}},
          ('key','kind','title','reason','scope','fallback','deadline')),
        t('owner_requests','List this self\'s requests. Paginate until next_offset is null.',{'offset':{'type':'integer'}}),
        t('owner_request_get','Read exact response, scope and verification state; supplied is not verified.',{'id':text('Request ID')},('id',)),
        t('owner_request_cancel','Cancel a pending request; never erase an owner response.',{'id':text('Request ID')},('id',)),
        t('owner_request_verify','Record actual tested enablement or failure, not assumed success from submission.',
          {'id':text('Request ID'),'ok':{'type':'boolean'},'evidence':text('What was tested and observed; no raw secrets')},('id','ok','evidence')),
        t('owner_secret_read','Retrieve this self\'s credential as private working information. Never copy into graph, public output or logs. For files use materialize. Maximum inline value 8 KiB.',
          {'id':text('Credential reference'),'key':text('Stable retrieval key for single-use credential')},('id',)),
        t('owner_secret_materialize','Write a credential/file into private instance runtime; returns path, never value.',
          {'id':text('Credential reference'),'key':text('Stable retrieval key')},('id',)),
        t('owner_secret_revoke','Revoke a credential from future retrieval; cannot erase copies already used.',{'id':text('Credential reference')},('id',)),
        t('business_dependencies','List dependency inventory (not a bank ledger).',{'offset':{'type':'integer'}}),
        t('business_dependency_put','Create/update a dependency with optimistic revision. Does not purchase anything.',
          {'id':text('Stable dependency ID'),'record':{'type':'object','description':'revision, name, purpose, account_ref?, credential_ref?, state (considering/purchased/awaiting_enablement/working/degraded/cancelled), currency (ISO uppercase), recurring_minor (integer), period (none/month/year/usage), next_review? (Unix seconds), cancellation?, commitments?[], request_ids?[], evidence'}},('id','record')),
        t('business_billing_record','Append an observed invoice/payment/refund/credit/commitment/cancellation receipt. Does not execute payment.',
          {'record':{'type':'object','description':'key, dependency, provider_ref, kind, amount_minor (nonnegative integer), currency, evidence'}},('record',)),
        t('business_billing','List immutable recorded billing evidence.',{'offset':{'type':'integer'}}),
    ]
    # Full argument contracts, not opaque record blobs: usable by any MCP harness.
    dependency=obj({'revision':{'type':'integer','minimum':0},'name':text('Dependency name'),
        'purpose':text('Why the business relies on this'),'account_ref':text('Nonsecret account identifier'),
        'credential_ref':text('Existing credential reference owned by this self, never raw value'),
        'state':{'type':'string','enum':['considering','purchased','awaiting_enablement','working','degraded','cancelled']},
        'currency':text('Three-letter uppercase currency code'),'recurring_minor':{'type':'integer','minimum':0},
        'period':{'type':'string','enum':['none','month','year','usage']},'next_review':{'type':['number','null'],'description':'Unix seconds; emits one observation per revision when due'},
        'cancellation':text('Cancellation terms and method'),'commitments':{'type':'array','items':{'type':'string'},'maxItems':30},
        'request_ids':{'type':'array','items':{'type':'string'},'maxItems':30},'evidence':text('Observed provider evidence, not an assumption')},
        ('revision','name','purpose','state','currency','recurring_minor','period','evidence'))
    billing=obj({'key':text('Stable 8–160-character idempotency key'),'dependency':text('Existing dependency ID'),
        'provider_ref':text('Actual provider invoice/payment identifier'),'kind':{'type':'string','enum':['invoice','payment','refund','credit','commitment','cancellation']},
        'amount_minor':{'type':'integer','minimum':0},'currency':text('Three-letter uppercase currency code'),
        'evidence':text('What was actually observed; recording never executes a payment')},
        ('key','dependency','provider_ref','kind','amount_minor','currency','evidence'))
    for tool in result:
        if tool['name']=='business_dependency_put':tool['inputSchema']['properties']['record']=dependency
        if tool['name']=='business_billing_record':tool['inputSchema']['properties']['record']=billing
    return result


def execute(client,name,a,instance):
    quote=lambda x:urllib.parse.quote(x,safe='')
    if name=='owner_request': return client.call('POST','/v1/requests',a)
    if name=='owner_requests': return client.call('GET','/v1/requests?offset='+str(max(0,int(a.get('offset',0)))))
    if name=='owner_request_get': return client.call('GET','/v1/requests/'+quote(a['id']))
    if name=='owner_request_cancel': return client.call('POST','/v1/requests/'+quote(a['id'])+'/cancel',{})
    if name=='owner_request_verify': return client.call('POST','/v1/requests/'+quote(a['id'])+'/verify',{'ok':a['ok'],'evidence':a['evidence']})
    if name=='owner_secret_revoke': return client.call('POST','/v1/credentials/'+quote(a['id'])+'/revoke',{})
    if name in ('owner_secret_read','owner_secret_materialize'):
        value=client.call('POST','/v1/credentials/'+quote(a['id'])+'/read',{'key':a.get('key','')})
        if name=='owner_secret_read':
            if len(value['value'].encode())>8192: raise ValueError('Value exceeds inline limit; use owner_secret_materialize with the same retrieval key')
            return value
        import base64
        import hashlib
        root=Path(instance)/'.concorde2'/'owner'/'secrets'
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=root/hashlib.sha256(a['id'].encode()).hexdigest()
        content=base64.b64decode(value['value'],validate=True) if value['encoding']=='base64' else value['value'].encode()
        atomic(target,content)
        return {'path':str(target),'bytes':len(content),'sensitive':True}
    if name=='business_dependencies': return client.call('GET','/v1/dependencies?offset='+str(max(0,int(a.get('offset',0)))))
    if name=='business_dependency_put': return client.call('PUT','/v1/dependencies/'+quote(a['id']),a['record'])
    if name=='business_billing_record': return client.call('POST','/v1/billing',a['record'])
    if name=='business_billing': return client.call('GET','/v1/billing?offset='+str(max(0,int(a.get('offset',0)))))
    raise ValueError('Unknown tool')


def atomic(path,content):
    import tempfile
    fd,temp=tempfile.mkstemp(prefix='.owner-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def check_actor(binary,instance,actor):
    from datetime import datetime, timezone
    chunks=[]; offset=0
    for _ in range(64):
        proc=subprocess.run([binary,'call',instance,'record',json.dumps({'section':'activations','id':actor,'offset':offset,'limit':16000})],capture_output=True,timeout=10)
        if proc.returncode: raise ValueError('Cannot verify activation authority')
        part=json.loads(proc.stdout);chunks.append(part['text'])
        if part.get('next_offset') is None or part['next_offset']<0: break
        offset=part['next_offset']
    else: raise ValueError('Activation record exceeds inspection bound')
    value=json.loads(''.join(chunks))
    if value.get('phase')!='work' or value.get('status')!='running' or value.get('completion') or datetime.fromisoformat(value['deadline'].replace('Z','+00:00'))<=datetime.now(timezone.utc):
        raise ValueError('Owner capability effects require an unexpired running work turn')
    proc=subprocess.run([binary,'call',instance,'state',json.dumps({'section':'config'})],capture_output=True,timeout=10)
    if proc.returncode: raise ValueError('Cannot verify instance authority')
    state=json.loads(proc.stdout);freeze=state['config'].get('freeze_at')
    if state['mode']=='frozen' or (freeze and not freeze.startswith('0001-') and datetime.fromisoformat(freeze.replace('Z','+00:00'))<=datetime.now(timezone.utc)):
        raise ValueError('Instance is frozen or past its run deadline')


def bridge_once(client,instance,intention,binary,cursor):
    # Cursor advances only after the existing idempotent notify has committed.
    # A replay after a crash coalesces at C3, not at an in-memory flag here.
    previous=int(cursor.read_text()) if cursor.exists() else 0
    events=client.call('GET','/v1/events?after='+str(previous))['events']
    for event in events:
        if event['kind']!='created':
            evidence=f"Owner request {event['request']} changed to {event['kind']}. Read it through owner_request_get; verify actual access before relying on it."
            if event['kind']=='dependency_review':
                evidence=f"Dependency {event['request']} reached its recorded review time. Read business_dependencies and relevant provider evidence; a due review is not proof of a fault."
            proc=subprocess.run([binary,'notify',str(instance),intention,
                'owner-'+client.config['instance']+'-'+str(event['seq']),evidence],capture_output=True,timeout=15)
            if proc.returncode:
                raise ValueError('C3 could not accept the observation; cursor retained (check pause/freeze/intention)')
        atomic(cursor,str(event['seq']).encode())
    return len(events)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--connection',required=True)
    p.add_argument('--instance',required=True)
    p.add_argument('--binary',default='concorde3')
    p.add_argument('--development',action='store_true')
    sub=p.add_subparsers(dest='mode',required=True)
    sub.add_parser('mcp')
    b=sub.add_parser('bridge'); b.add_argument('--intention',required=True); b.add_argument('--once',action='store_true')
    args=p.parse_args()
    os.umask(0o077)
    client=Client(args.connection,args.development)
    if args.mode=='bridge':
        import fcntl
        root=Path(args.instance)/'.concorde2'/'owner'; root.mkdir(parents=True,exist_ok=True,mode=0o700)
        # One feed cursor per target, with a process lock to prevent reverse cursor races.
        import hashlib
        key=hashlib.sha256((client.config['instance']+args.intention).encode()).hexdigest()
        with open(root/(key+'.lock'),'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            while True:
                try: bridge_once(client,args.instance,args.intention,args.binary,root/(key+'.cursor'))
                except Exception:
                    if args.once: raise
                    print('Owner observation delivery pending; retrying.',file=sys.stderr)
                if args.once: break
                time.sleep(5)
        return
    for line in sys.stdin:
        req={}
        try:
            if len(line)>1_600_000: raise ValueError('Input exceeds capability bound')
            req=json.loads(line)
            if 'id' not in req: continue
            method=req.get('method')
            if method=='initialize':
                result={'protocolVersion':'2024-11-05','serverInfo':{'name':'concorde-owner','version':'1.0'},'capabilities':{'tools':{}}}
            elif method=='tools/list': result={'tools':tools()}
            elif method=='ping': result={}
            elif method=='tools/call':
                a=req['params']; name=a['name']
                # The external capability observes C3's current phase without owning it.
                actor=os.environ.get('CONCORDE3_ACTIVATION')
                if actor and name not in ('owner_requests','owner_request_get','business_dependencies','business_billing'):
                    check_actor(args.binary,args.instance,actor)
                result={'content':[{'type':'text','text':json.dumps(execute(client,name,a.get('arguments',{}),args.instance))}],'isError':False}
            else: raise ValueError('Unknown MCP method')
        except Exception as e:
            # Do not echo arbitrary library exception strings that might include secrets.
            message=str(e) if isinstance(e,ValueError) and not isinstance(e,json.JSONDecodeError) else 'Capability call failed; check arguments and connection'
            result={'content':[{'type':'text','text':message}],'isError':True}
        print(json.dumps({'jsonrpc':'2.0','id':req.get('id'),'result':result}),flush=True)


if __name__=='__main__': main()

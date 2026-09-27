import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from .app import create_app
from .store import Store, Fault, packed, digest


@pytest.fixture
def env(tmp_path):
    now=[1800000000.0]
    s=Store(tmp_path,clock=lambda:now[0])
    a,ta=s.add_instance('Test instance A')
    b,tb=s.add_instance('Test instance B')
    app=create_app(s,'https://owner.example.test','owner@example.test')
    c=TestClient(app,base_url='https://owner.example.test')
    raw,csrf=s.session('owner')
    c.cookies.set('__Host-owner_session',raw)
    headers={'origin':'https://owner.example.test','x-csrf-token':csrf}
    return s,c,a,ta,b,tb,headers,now


def body(now,kind='decision',key='test-request-key'):
    return dict(key=key,kind=kind,title='Test request',reason='Synthetic acceptance',scope='Only this synthetic action',
        fallback='Continue unrelated work',deadline=now+3600,secret_ttl=300,single_use=False,action_url='')


def make(env,kind='decision',key='test-request-key',**changes):
    s,c,a,ta,b,tb,h,now=env
    value=body(now[0],kind,key); value.update(changes)
    r=c.post('/v1/requests',headers={'authorization':'Bearer '+ta},json=value)
    assert r.status_code==200,r.text
    return r.json()


def reply(c,h,r,action='approve',**kw):
    data=dict(key='reply-idempotency-key',scope=r['scope'],action=action)
    data.update(kw)
    return c.post('/api/requests/'+r['id']+'/respond',headers=h,json=data)


def test_create_idempotency_scope_and_provenance(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env)
    assert make(env)['id']==r['id']
    assert r['scope_text']=='Only this synthetic action'
    bad=body(now[0]); bad['scope']='Different purchase'
    assert c.post('/v1/requests',headers={'authorization':'Bearer '+ta},json=bad).status_code==409
    assert len(s.events(a))==1


def test_instance_isolation(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env,'credential')
    secret=reply(c,h,r,'supply',secret='synthetic-private-value').json()['response']['credential']
    auth={'authorization':'Bearer '+tb}
    for method,path,data in [('GET','/v1/requests/'+r['id'],None),('POST','/v1/requests/'+r['id']+'/cancel',{}),
                            ('POST','/v1/credentials/'+secret+'/read',{}),('POST','/v1/credentials/'+secret+'/revoke',{})]:
        assert c.request(method,path,headers=auth,json=data).status_code==404
    assert c.get('/v1/requests',headers=auth).json()['items']==[]
    assert c.get('/v1/events',headers=auth).json()['events']==[]
    assert c.get('/api/requests',headers=auth,cookies={'__Host-owner_session':''}).status_code==401


def test_secret_encrypted_and_not_reflected(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env,'credential')
    value='synthetic-SUPER-PRIVATE-credential'
    response=reply(c,h,r,'supply',secret=value)
    assert response.status_code==200
    assert value not in response.text
    credential=response.json()['response']['credential']
    assert value not in c.get('/api/requests').text
    assert value not in packed(s.events(a))
    with s.db() as db:
        assert value not in '\n'.join(db.iterdump())
    assert value.encode() not in s.path.read_bytes()
    result=c.post('/v1/credentials/'+credential+'/read',headers={'authorization':'Bearer '+ta},json={})
    assert result.json()['value']==value
    reopened=Store(s.root,clock=lambda:now[0])
    assert reopened.secret(a,credential,'')['value']==value


def test_no_input_reflection_in_validation(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env,'credential')
    response=reply(c,h,r,'supply',secret={'oops':'secret-must-not-appear'})
    assert response.status_code==422
    assert 'secret-must-not-appear' not in response.text


def test_origin_csrf_and_host(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env)
    assert reply(c,{},r).status_code==403
    assert reply(c,dict(h,origin='https://evil.test'),r).status_code==403
    assert reply(c,dict(h,**{'x-csrf-token':'wrong'}),r).status_code==403
    assert c.get('/healthz',headers={'host':'evil.test'}).status_code==400
    assert c.get('/api/requests').headers['cache-control']=='no-store'
    assert 'frame-ancestors' in c.get('/api/requests').headers['content-security-policy']


def test_expired_cancelled_stale_and_wrong_kind(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env)
    assert reply(c,h,r,scope='0'*64).status_code==409
    assert reply(c,h,r,'supply',secret='no').status_code==422
    now[0]+=3601
    assert reply(c,h,r).status_code==409
    assert s.get(r['id'])['state']=='expired'
    r=make(env,key='test-cancelled-key')
    s.cancel(a,r['id'])
    assert reply(c,h,r).status_code==409
    assert s.get(r['id'])['state']=='cancelled'


def test_concurrent_response_wins_once(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env)
    def submit(i):
        try:
            return s.respond(r['id'],dict(key='concurrent-key-'+str(i),scope=r['scope'],action='approve',note=''))['state']
        except Fault as e: return e.status
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(submit,range(8)))
    assert results.count('supplied')==1
    assert results.count(409)==7
    assert len([x for x in s.events(a) if x['kind']=='supplied'])==1


def test_repeat_response_is_receipt_not_new_effect(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env)
    first=reply(c,h,r).json()
    assert reply(c,h,r).json()==first
    assert reply(c,h,r,'decline',key='different-key').status_code==409
    assert first['state']=='supplied' and first['verification'] is None
    assert s.verify(a,r['id'],True,'Synthetic probe passed')['state']=='verified'
    assert s.verify(a,r['id'],True,'Synthetic probe passed')['state']=='verified'
    with pytest.raises(Fault): s.verify(a,r['id'],False,'Rewrite')


def test_single_use_retrieval_replay_expiry_and_revocation(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env,'credential',single_use=True)
    ident=reply(c,h,r,'supply',secret='654321').json()['response']['credential']
    with pytest.raises(Fault): s.secret(a,ident,'')
    assert s.secret(a,ident,'claim-1')['value']=='654321'
    assert s.secret(a,ident,'claim-1')['value']=='654321'
    with pytest.raises(Fault): s.secret(a,ident,'claim-2')
    now[0]+=301
    with pytest.raises(Fault): s.secret(a,ident,'claim-1')
    r=make(env,'credential',key='another-secret-key')
    ident=reply(c,h,r,'supply',secret='other').json()['response']['credential']
    s.revoke(a,ident)
    with pytest.raises(Fault): s.secret(a,ident,'')


def test_file_validation_and_size(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env,'credential')
    assert reply(c,h,r,'supply',secret='!!!!',encoding='base64').status_code==422
    assert reply(c,h,r,'supply',secret='ok',filename='../bad').status_code==422
    assert c.post('/api/requests/'+r['id']+'/respond',headers=h,content=b'x'*1_600_001).status_code==413
    payload=base64.b64encode(b'file\x00content').decode()
    ident=reply(c,h,r,'supply',secret=payload,encoding='base64',filename='credential.bin').json()['response']['credential']
    assert s.secret(a,ident,'')['value']==payload


def test_enrollment_is_not_owner_access(env):
    s,c,a,ta,b,tb,h,now=env
    token=s.link('enroll')
    c.cookies.clear()
    result=c.post('/auth/redeem',headers={'origin':'https://owner.example.test'},json={'token':token})
    assert result.status_code==200
    assert 'Secure' in result.headers['set-cookie'] and 'HttpOnly' in result.headers['set-cookie']
    assert c.get('/api/requests').status_code==401
    assert c.post('/auth/redeem',headers={'origin':'https://owner.example.test'},json={'token':token}).status_code==401
    csrf=result.json()['csrf']
    options=c.post('/auth/register/options',headers=dict(h,**{'x-csrf-token':csrf}))
    assert options.status_code==200
    assert options.json()['options']['authenticatorSelection']['userVerification']=='required'


def test_invalid_passkey_consumes_challenge(env):
    s,c,a,ta,b,tb,h,now=env
    value=c.post('/auth/login/options',headers=h).json()
    data={'challenge_id':value['challenge_id'],'credential':{'id':'unknown'}}
    assert c.post('/auth/login/verify',headers=h,json=data).status_code==401
    with s.db() as db:
        assert not db.execute('SELECT 1 FROM challenges WHERE id=?',(value['challenge_id'],)).fetchone()


def test_rate_limit_and_logout(env):
    s,c,a,ta,b,tb,h,now=env
    for i in range(3): s.rate('test',3,60)
    with pytest.raises(Fault): s.rate('test',3,60)
    now[0]+=61
    s.rate('test',3,60)
    assert c.post('/auth/logout',headers=h).status_code==200
    assert c.get('/api/requests').status_code==401


def test_dependency_and_billing_revision_integrity(env):
    s,c,a,ta,b,tb,h,now=env
    headers={'authorization':'Bearer '+ta}
    dep=dict(revision=0,name='Test vendor',purpose='Synthetic dependency',state='considering',currency='USD',recurring_minor=1000,period='month',evidence='Test quote')
    response=c.put('/v1/dependencies/test-vendor',headers=headers,json=dep)
    assert response.status_code==200,response.text
    assert c.put('/v1/dependencies/test-vendor',headers=headers,json=dep).status_code==409
    bill=dict(key='provider-payment-key',dependency='test-vendor',provider_ref='test-001',kind='payment',amount_minor=1000,currency='USD',evidence='Synthetic receipt')
    one=c.post('/v1/billing',headers=headers,json=bill)
    assert one.status_code==200
    assert c.post('/v1/billing',headers=headers,json=bill).json()==one.json()
    assert c.post('/v1/billing',headers=headers,json=dict(bill,amount_minor=2000)).status_code==409
    assert c.get('/v1/billing',headers={'authorization':'Bearer '+tb}).json()['items']==[]
    assert c.post('/v1/billing',headers=headers,json=dict(bill,amount_minor=10.5)).status_code==422


def test_key_loss_does_not_silently_regenerate(env):
    s,*_=env
    (s.root/'vault.key').rename(s.root/'saved.key')
    with pytest.raises(ValueError): Store(s.root)
    assert not (s.root/'vault.key').exists()


def test_external_links_and_disabled_instance(env):
    s,c,a,ta,b,tb,h,now=env
    headers={'authorization':'Bearer '+ta}
    assert c.post('/v1/requests',headers=headers,json=dict(body(now[0]),action_url='javascript:alert(1)')).status_code==422
    with s.db() as db: db.execute('UPDATE instances SET enabled=0 WHERE id=?',(a,))
    assert c.get('/v1/requests',headers=headers).status_code==401

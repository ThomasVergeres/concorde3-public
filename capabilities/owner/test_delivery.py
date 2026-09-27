"""Provider delivery is mocked; these tests send no emails or payments."""
import httpx
import pytest

from .notify import Notifier
from .store import Fault
from .test_owner import env,make,reply


def test_proposal_is_not_approval(env):
    s,c,a,ta,b,tb,h,now=env
    r=make(env)
    assert reply(c,h,r,'propose').status_code==422
    assert reply(c,h,r,'propose',note='Only approve a lower monthly limit').json()['state']=='proposed'
    with pytest.raises(Fault):s.verify(a,r['id'],True,'This must not turn a proposal into authority')
    assert reply(c,h,r,'approve',key='new-response-key').status_code==409


def test_review_observation_is_once_per_revision(env):
    s,c,a,ta,b,tb,h,now=env
    data=dict(name='Test subscription',purpose='Synthetic dependency',state='working',currency='USD',recurring_minor=300,period='month',next_review=now[0]+60,evidence='Test evidence')
    s.dependency(a,'test-service',0,data)
    assert s.events(a)==[]
    now[0]+=61
    assert len(s.events(a))==1
    assert s.events(a)[0]['kind']=='dependency_review'
    assert s.events(b)==[]
    s.dependency(a,'test-service',1,dict(data,next_review=now[0]+60))
    assert len(s.events(a))==1
    now[0]+=61
    assert len(s.events(a))==2


def test_notification_receipt_retries_same_key_and_redacts_content(env,monkeypatch):
    s,c,a,ta,b,tb,h,now=env
    r=make(env,'credential',title='Private title must not enter email')
    sent=[]
    def send(url,**kwargs):
        sent.append(kwargs)
        if len(sent)==1:raise httpx.ReadTimeout('ambiguous provider outcome')
        return httpx.Response(200,json={'id':'synthetic-provider-receipt'})
    monkeypatch.setattr(httpx,'post',send)
    n=Notifier(s,'https://owner.example.test','owner@example.test','sender@example.test','synthetic-key')
    assert n.once()
    assert not n.once()
    now[0]+=61
    assert n.once()
    assert sent[0]['json']==sent[1]['json']
    assert sent[0]['headers']['Idempotency-Key']==sent[1]['headers']['Idempotency-Key']
    assert 'Private title' not in str(sent[0]['json'])
    with s.db() as db:
        row=db.execute('SELECT * FROM outbox').fetchone()
        assert row['state']=='sent' and row['receipt']=='synthetic-provider-receipt' and row['payload']==b''


def test_notification_expiry_stops_retries(env,monkeypatch):
    s,c,a,ta,b,tb,h,now=env
    make(env)
    now[0]+=24*3600
    monkeypatch.setattr(httpx,'post',lambda *a,**k:pytest.fail('expired mail must not be sent'))
    assert Notifier(s,'https://owner.example.test','owner@example.test','sender@example.test','synthetic-key').once()
    with s.db() as db:assert db.execute('SELECT state FROM outbox').fetchone()[0]=='expired'

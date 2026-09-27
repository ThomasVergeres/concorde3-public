import json
import os
from pathlib import Path
import subprocess
import sys

from .adapter import Client, bridge_once, atomic
from .cli import private_write
from .attach import attach
from .test_browser import live


def test_complete_c3_loop_and_replayed_wake(live,tmp_path):
    s,origin=live
    repo=Path(__file__).resolve().parents[2]
    binary=tmp_path/'concorde3'
    subprocess.run(['go','build','-o',str(binary),'./cmd/concorde3'],cwd=repo,check=True,capture_output=True)
    instance=tmp_path/'self'
    def cmd(*args):
        return subprocess.run([str(binary),*map(str,args)],capture_output=True,check=True).stdout
    cmd('init','--workspace',instance)
    ident=json.loads(cmd('status',instance))['id']
    _,token=s.add_instance('Test C3 integration',ident)
    root=instance/'.concorde2'/'owner';root.mkdir(mode=0o700)
    connection=root/'connection.json'
    private_write(connection,{'origin':origin,'instance':ident,'token':token})
    cmd('configure',instance,json.dumps({'mcp':{'existing':{'command':['/bin/true'],'approval':'approve'}}}))
    attach(instance,connection,str(binary),development=True)
    attach(instance,connection,str(binary),development=True)
    config=json.loads(cmd('call',instance,'state',json.dumps({'section':'config'})))['config']
    assert set(config['mcp'])=={'existing','owner'}
    assert config['global'].count('owner-capability')==1
    # The bridge is exercised explicitly here to make crash/replay timing deterministic.
    cmd('program-stop',instance,'owner-observer')
    # Python module resolution in the command process is explicit, not ambient config.
    harness=repo/'capabilities'/'owner'/'fixture_harness.py'
    command=[sys.executable,'-c',f"import sys;sys.path.insert(0,{str(repo)!r});from capabilities.owner.fixture_harness import main;main()",str(binary)]
    cmd('configure',instance,json.dumps({'harness':'command','command':command,'starts_per_hour':20,'deadline_seconds':30}))
    cmd('resume',instance);cmd('pulse',instance)
    requests=s.listing(ident)['items'];assert len(requests)==1
    first=json.loads(cmd('status',instance))
    assert len(first['activations'])==1
    assert list(first['activations'].values())[0]['status']=='completed'
    assert first['attention']['purpose']['status']=='dormant'
    r=requests[0]
    s.respond(r['id'],dict(key='owner-test-response',scope=r['scope'],action='supply',secret='fixture-secret',note=''))
    client=Client(connection,development=True);cursor=root/'test.cursor'
    assert bridge_once(client,instance,'purpose',str(binary),cursor)>0
    # Replay after hypothetical crash after notify/before cursor write.
    atomic(cursor,b'0')
    bridge_once(client,instance,'purpose',str(binary),cursor)
    wakes=json.loads(cmd('call',instance,'state',json.dumps({'section':'wakes'})))
    assert len(wakes['items'])==1,wakes
    # A new process (not retained conversation) admits the wake, works, rectifies.
    cmd('pulse',instance)
    second=json.loads(cmd('status',instance))
    assert len(second['activations'])==2
    assert all(a['status']=='completed' for a in second['activations'].values())
    assert s.get(r['id'])['state']=='verified'
    journal=(instance/'.concorde2'/'events.jsonl').read_text()
    assert 'fixture-secret' not in journal
    cmd('freeze',instance)
    # Terminal freeze cannot be evaded by an owner response.
    import pytest
    with pytest.raises(ValueError):bridge_once(client,instance,'purpose',str(binary),cursor)
    assert json.loads(cmd('status',instance))['mode']=='frozen'

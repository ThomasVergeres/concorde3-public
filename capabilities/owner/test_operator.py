import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from .adapter import tools
from .store import Store, Fault


def test_operator_setup_rotation_disable_and_no_secret_stdout(tmp_path):
    repo=Path(__file__).resolve().parents[2]
    data=tmp_path/'state';config=tmp_path/'config.json';dotenv=tmp_path/'.env'
    dotenv.write_text('RESEND_API_KEY=synthetic-mail-key\nRESEND_RECEIVING_DOMAIN=example.test\nUNRELATED_KEY=must-not-copy\n')
    def cli(*args,ok=True):
        proc=subprocess.run([sys.executable,'-m','capabilities.owner.cli','--data',str(data),*map(str,args)],cwd=repo,capture_output=True,text=True)
        assert (proc.returncode==0)==ok,proc.stderr
        assert 'synthetic-mail-key' not in proc.stdout and 'must-not-copy' not in proc.stdout
        return proc
    cli('setup','--origin','https://owner.example.test','--email','owner@example.test','--dotenv',dotenv,'--output',config)
    assert 'must-not-copy' not in config.read_text()
    assert config.stat().st_mode&0o077==0
    cli('setup','--origin','https://owner.example.test','--email','owner@example.test','--dotenv',dotenv,'--output',config,ok=False)
    connection=tmp_path/'connection.json'
    cli('register','--name','Test self','--instance-id','test-self','--origin','https://owner.example.test','--output',connection)
    store=Store(data);old=json.loads(connection.read_text())['token']
    assert store.instance(old)=='test-self'
    cli('disable','--instance-id','test-self')
    with pytest.raises(Fault):store.instance(old)
    replacement=tmp_path/'replacement.json'
    cli('rotate','--instance-id','test-self','--origin','https://owner.example.test','--output',replacement)
    assert store.instance(json.loads(replacement.read_text())['token'])=='test-self'
    with pytest.raises(Fault):store.instance(old)


def test_business_tools_have_structured_argument_contracts():
    catalog={t['name']:t for t in tools()}
    for name,field in [('business_dependency_put','revision'),('business_billing_record','provider_ref')]:
        schema=catalog[name]['inputSchema']['properties']['record']
        assert field in schema['properties'] and field in schema['required']
        assert schema['additionalProperties'] is False

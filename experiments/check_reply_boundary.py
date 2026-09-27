"""Disposable Docker integration check; never changes a running experiment."""
import argparse
import http.client
import json
import socket
import time
import uuid
from evals.lab import command
from .incubator_reply_boundary import rule


def get(host):
    c=http.client.HTTPConnection(host,8000,timeout=1)
    try:
        c.request('GET','/')
        r=c.getresponse()
        return r.status,r.read()
    finally:c.close()


def check(image):
    name='c3-reply-check-'+uuid.uuid4().hex[:12]
    rules=[]
    network_created=container_created=False
    try:
        command(['docker','network','create','--internal',name])
        network_created=True
        net=json.loads(command(['docker','network','inspect',name]))[0]
        bridge='br-'+net['Id'][:12]
        gateway=net['IPAM']['Config'][0]['Gateway']
        server='from http.server import HTTPServer,BaseHTTPRequestHandler\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"qualification only")\nHTTPServer(("0.0.0.0",8000),H).serve_forever()'
        command(['docker','run','-d','--name',name,'--network',name,'--read-only',
                 '--memory','128m','--cpus','.25','--pids-limit','32','--cap-drop','ALL',
                 '--security-opt','no-new-privileges','--entrypoint','python3',image,'-c',server])
        container_created=True
        info=json.loads(command(['docker','inspect',name]))[0]
        host=info['NetworkSettings']['Networks'][name]['IPAddress']
        for attempt in range(20):
            try:
                if get(host)==(200,b'qualification only'):break
            except OSError:pass
            time.sleep(.1)
        else:raise RuntimeError('fixture did not serve before isolation')
        old=['-i',bridge,'-m','comment','--comment','private-incubator-daylight','-j','REJECT']
        new=rule(bridge,'daylight')
        command(['sudo','-n','iptables','-I','INPUT',*old]);rules.append(old)
        try:get(host)
        except OSError:old_failed=True
        else:raise AssertionError('old blanket rejection did not reproduce failure')
        command(['sudo','-n','iptables','-I','INPUT',*new]);rules.append(new)
        command(['sudo','-n','iptables','-D','INPUT',*old]);rules.remove(old)
        assert get(host)==(200,b'qualification only')
        # Listening socket proves refusal is isolation, not a closed host port.
        with socket.socket() as listener:
            listener.bind((gateway,0));listener.listen(1)
            port=listener.getsockname()[1]
            probe='import socket; s=socket.socket(); s.settimeout(2); rc=s.connect_ex(('+repr(gateway)+','+str(port)+')); s.close(); print(rc); assert rc!=0'
            denied=command(['docker','exec',name,'python3','-c',probe],timeout=5)
        return {'old_host_request_failed':old_failed,'repaired_host_request_status':200,
                'new_subject_host_connection_errno':int(denied),'fixture':name,
                'live_worlds_touched':False}
    finally:
        for r in rules:command(['sudo','-n','iptables','-D','INPUT',*r])
        if container_created:command(['docker','rm','-f',name])
        if network_created:command(['docker','network','rm',name])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',required=True)
    print(json.dumps(check(p.parse_args().image),indent=2))

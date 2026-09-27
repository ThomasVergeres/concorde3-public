"""Permit replies to host-initiated connections, never subject-initiated host access."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from .private_incubator import read, log
from evals.lab import command, save


def rule(bridge, name):
    if not re.fullmatch(r'br-[0-9a-f]{12}', bridge) or name not in ('harbor', 'daylight', 'loom', 'keel', 'mosaic', 'weft'):
        raise ValueError('explicit incubator bridge/company required')
    return ['-i', bridge, '-m', 'conntrack', '!', '--ctstate', 'ESTABLISHED',
            '-m', 'comment', '--comment', 'private-incubator-'+name, '-j', 'REJECT']


def apply(root, name):
    root = Path(root)
    m = read(root/'cohort.json')
    if m.get('experiment') != 'terra-business-20260915' or m.get('status') != 'running':
        raise ValueError('live Terra cohort required')
    path = root/(name+'-host-boundary.json')
    old = read(path)
    dep = read(Path(m['worlds'][name])/'deployment.json')
    actor = m['arms'][name]['subject']
    network = json.loads(command(['docker', 'network', 'inspect', dep['networks'][actor]['network']]))[0]
    bridge = 'br-'+network['Id'][:12]
    if not network['Internal'] or old['bridge'] != bridge:
        raise ValueError('boundary does not match current internal network')
    new = rule(bridge, name)
    if old['rule'] == new:
        command(['sudo', '-n', 'iptables', '-C', 'INPUT', *new])
        return {'status': 'already_applied'}
    expected = ['-i', bridge, '-m', 'comment', '--comment', 'private-incubator-'+name, '-j', 'REJECT']
    if old['rule'] != expected:
        raise ValueError('unexpected prior rule; inspect before changing')
    command(['sudo', '-n', 'iptables', '-C', 'INPUT', *expected])
    # Install before removal: no gap allowing new subject connections.
    command(['sudo', '-n', 'iptables', '-I', 'INPUT', *new])
    try:
        command(['sudo', '-n', 'iptables', '-D', 'INPUT', *expected])
        save(path, {**old, 'rule': new, 'prior_rule': expected,
                    'basis': 'Reject all non-established inbound traffic; host-request replies permitted'})
    except BaseException:
        # Restore rejection before removing the replacement, including save errors.
        if subprocess.run(['sudo', '-n', 'iptables', '-C', 'INPUT', *expected], capture_output=True).returncode:
            command(['sudo', '-n', 'iptables', '-I', 'INPUT', *expected])
        command(['sudo', '-n', 'iptables', '-D', 'INPUT', *new])
        raise
    log(root, 'host_reply_boundary_repair', company=name, bridge=bridge, before=expected, after=new,
        reason='Host-side counterpart use timed out while world-container GET succeeded; blanket INPUT rejection blocked return traffic')
    return {'status': 'applied', 'company': name, 'bridge': bridge}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('name', choices=['harbor', 'daylight', 'loom'])
    a = p.parse_args()
    print(json.dumps(apply(a.root, a.name)))

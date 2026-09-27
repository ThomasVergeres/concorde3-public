"""Extend the existing private relay without resetting inboxes or receipts."""
import datetime as dt
import fcntl
import json
from pathlib import Path
import sys
from experiments.forge import BIN,run,save
from experiments.merit import PROTOCOL
from experiments.studio_relay import safe_path


def connect(merit_root,relay_root):
    m=json.loads((merit_root/'manifest.json').read_text())
    if m['status']!='running':raise RuntimeError('Merit must be running')
    with (relay_root/'relay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        cfg=json.loads((relay_root/'config.json').read_text())
        if 'merit' in cfg['instances']:raise RuntimeError('Merit already connected')
        base=merit_root/'instance'
        for part in ('collaboration/outbox','collaboration/inbox','collaboration/receipts','production-requests'):
            safe_path(base,part).mkdir(parents=True,exist_ok=True)
        save(safe_path(base,'collaboration/inbox/index.json'),dict(messages=[]))
        cfg['instances']['merit']=str(base)
        cfg['peers']={'signal':['atelier','merit'],'atelier':['signal'],'merit':['signal']}
        cfg['cutoff']=max(cfg['cutoff'],dt.datetime.fromisoformat(m['cutoff']).timestamp())
        for actor in ('signal','merit'):
            safe_path(Path(cfg['instances'][actor]),'MERIT_CHANNEL.md').write_text(PROTOCOL)
        save(relay_root/'config.json',cfg)
    # Replace only the older final-stop timer, not the live relay or checkpoints.
    run('sudo','-n','systemctl','stop','c3-studio-relay-20260925-cutoff.timer')
    run('sudo','-n','systemd-run','--quiet','--unit','c3-studio-relay-merit-cutoff',
        '--on-calendar',dt.datetime.fromtimestamp(cfg['cutoff']+60,dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        '/usr/bin/systemctl','stop','c3-studio-relay-20260925.timer')
    for actor in ('signal','merit'):
        run(BIN,'notify',cfg['instances'][actor],'purpose','operator.merit-channel-20260925',
            'The owner authorized Merit, the affiliate product diligence and offer-index business, '
            'to collaborate with Signal for evidence-based distribution. Read /instance/MERIT_CHANNEL.md. '
            'Signal now has TWO authorized peers: Atelier and Merit; Merit may contact Signal only. '
            'This extends older single-peer instructions; use the existing collaboration outbox and inbox. '
            'Do not overwrite old exchanges. No peer approves production or spending. Merit must obtain '
            'the owner\'s approval for EACH product before production. Share product evidence, concrete briefs '
            'and source artifacts, not private graph access. Await explicit operator approval, never silence.')


if __name__=='__main__':connect(*(Path(x).resolve() for x in sys.argv[1:]))

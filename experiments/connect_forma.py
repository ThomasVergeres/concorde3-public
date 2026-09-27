"""Extend the private studio relay without replacing existing peer relations."""
import datetime as dt
import fcntl
import json
from pathlib import Path
import sys
from experiments.forge import BIN, run, save
from experiments.forma import PROTOCOL
from experiments.studio_relay import safe_path


def connect(root, relay):
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest['status']!='running': raise RuntimeError('Forma must be running')
    with (relay/'relay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        cfg=json.loads((relay/'config.json').read_text())
        if 'forma' in cfg['instances']: raise RuntimeError('Forma already connected; preserve existing channel')
        if 'atelier' not in cfg['instances']: raise RuntimeError('Atelier is unavailable')
        base=root/'instance'
        for part in ('collaboration/outbox','collaboration/inbox','collaboration/receipts'):
            safe_path(base,part).mkdir(parents=True,exist_ok=True)
        index=safe_path(base,'collaboration/inbox/index.json')
        if not index.exists(): save(index,dict(messages=[]))
        cfg['instances']['forma']=str(base)
        peers=cfg.setdefault('peers',{})
        peers['forma']=['atelier']
        peers.setdefault('atelier',[]).append('forma')
        cfg['cutoff']=max(cfg['cutoff'],dt.datetime.fromisoformat(manifest['cutoff']).timestamp())
        for actor in ('forma','atelier'):
            safe_path(Path(cfg['instances'][actor]),'FORMA_CHANNEL.md').write_text(PROTOCOL)
        save(relay/'config.json',cfg)
    run('sudo','-n','systemctl','stop','c3-studio-relay-merit-cutoff.timer')
    run('sudo','-n','systemd-run','--quiet','--unit','c3-studio-relay-forma-cutoff',
        '--on-calendar',dt.datetime.fromtimestamp(cfg['cutoff']+60,dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        '/usr/bin/systemctl','stop','c3-studio-relay-20260925.timer')
    for actor in ('forma','atelier'):
        run(BIN,'notify',cfg['instances'][actor],'purpose','operator.forma-channel-20260925',
            'Forma, the new cohesive design-system business, and Atelier may collaborate through '
            'the private relay. Read /instance/FORMA_CHANNEL.md. Forma owns a priced portfolio '
            'of full application/site designs and bespoke system packages; Atelier can offer '
            'craft feedback or collaboration without abandoning its undertaking. Share concrete '
            'briefs and inspectable artifacts. This adds one authorized peer, not public outreach '
            'or spending/sales approval. Preserve existing relationships and evidence.')


if __name__ == '__main__': connect(*(Path(x).resolve() for x in sys.argv[1:]))

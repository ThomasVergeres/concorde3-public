"""Run behind a TLS proxy; secrets are loaded from private operator configuration."""
import json
import os
from pathlib import Path
import uvicorn

from .app import create_app
from .store import Store
from .notify import Notifier


def configured_app():
    os.umask(0o077)
    path=Path(os.environ['CONCORDE_OWNER_CONFIG'])
    if path.stat().st_mode & 0o077:
        raise ValueError('operator config must be private')
    cfg=json.loads(path.read_text())
    store=Store(cfg['data'],cfg.get('key_file'))
    notifier=None
    if cfg.get('resend_key'):
        notifier=Notifier(store,cfg['origin'],cfg['email'],cfg['sender'],cfg['resend_key'])
    return create_app(store,cfg['origin'],cfg['email'],notifier)


if __name__=='__main__':
    uvicorn.run('capabilities.owner.serve:configured_app',factory=True,
                host='127.0.0.1',port=int(os.getenv('CONCORDE_OWNER_PORT','8765')),
                access_log=False,proxy_headers=False)

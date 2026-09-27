"""Narrow private artifact relay and operator-authorized cost-proposal email.

Never executes peer content, purchases, treats email as approval or reads replies.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from experiments.forge import save

FIELDS = ('question','method','provider','maximum_cost','duration','expected_value','alternatives','risks')
PRODUCT_FIELDS = ('product_id','product_version','merchant','offer','quality_evidence',
                  'ethical_review','affiliate_terms','economics','proposed_claims',
                  'disclosures','channels','costs','risks','launch_scope','evidence_refs')
class RelayConfigError(RuntimeError):
    pass


def email_settings():
    """Private relay settings supplied by the host, never by an instance."""
    names = ('CONCORDE_OWNER_EMAIL', 'CONCORDE_RESEND_HELPER', 'CONCORDE_RESEND_DOTENV')
    values = [os.environ.get(name, '').strip() for name in names]
    if not all(values):
        raise RelayConfigError('set CONCORDE_OWNER_EMAIL, CONCORDE_RESEND_HELPER and CONCORDE_RESEND_DOTENV')
    email, helper, dotenv = values
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email):
        raise RelayConfigError('CONCORDE_OWNER_EMAIL must be one mailbox address')
    if not Path(helper).is_file() or not Path(dotenv).is_file():
        raise RelayConfigError('configured email helper and dotenv must exist')
    return email, helper, dotenv


def safe_path(base, relative):
    base = base.resolve()
    path = base / relative
    if not path.resolve().is_relative_to(base) or path.is_symlink():
        raise ValueError('path escapes instance or is symlink')
    return path


def read_envelope(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError('regular JSON file required')
        raw = f.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('envelope too large')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def validate_message(value, peer):
    if not isinstance(value,dict) or value.get('to') != peer:
        raise ValueError('only configured peer may receive messages')
    if not isinstance(value.get('body'),str) or not 1 <= len(value['body']) <= 20_000:
        raise ValueError('body required, maximum 20k characters')
    files=value.get('files',{})
    if not isinstance(files,dict) or len(files)>30:
        raise ValueError('at most 30 text files')
    for name,body in files.items():
        if not re.fullmatch(r'[A-Za-z0-9_./-]{1,150}',name) or '..' in name.split('/') or name.startswith('/') or not isinstance(body,str):
            raise ValueError('safe relative filename labels and text required')
    return value


def proposal(value, production=False):
    fields=PRODUCT_FIELDS if production else FIELDS
    if not isinstance(value,dict) or set(value)!=set(fields):
        raise ValueError('exact proposal fields required; no recipients or attachments')
    if any(not isinstance(value[k],str) or not value[k].strip() or len(value[k])>4000 for k in fields):
        raise ValueError('nonempty proposal strings required, max 4000 each')
    heading=('Merit requests approval BEFORE promoting this specific product in production.\n'
             if production else 'Signal requests approval BEFORE incurring a research cost.\n')
    return (heading+
            'No spending is authorized by this email. Please approve/reject in the Codex chat; '
            'email replies are not automatically ingested. No publication or product endorsement '
            'is authorized by sending this request. Approval must name the exact scope.\n\n'+
            '\n\n'.join(k.replace('_',' ').title()+':\n'+value[k] for k in fields))


def tick(root):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'relay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cfg=json.loads((root/'config.json').read_text())
        ledger=json.loads((root/'ledger.json').read_text()) if (root/'ledger.json').exists() else {}
        if time.time()>=cfg['cutoff']:
            return
        for actor,instance in cfg['instances'].items():
            base=Path(instance)
            if json.loads((base/'.concorde2/state.json').read_text())['mode']=='frozen':continue
            for kind in ('collaboration/outbox','cost-requests','production-requests'):
                if kind=='cost-requests' and actor!='signal':continue
                if kind=='production-requests' and actor!='merit':continue
                folder=safe_path(base,kind);folder.mkdir(parents=True,exist_ok=True)
                pending=[p for p in sorted(folder.glob('*.json')) if actor+':'+kind+':'+p.name not in ledger]
                for file in pending[:100]:
                    key=actor+':'+kind+':'+file.name
                    if key in ledger:continue
                    try:
                        value,digest=read_envelope(safe_path(base,str(file.relative_to(base))))
                        if kind=='collaboration/outbox':
                            peers=cfg.get('peers',{'signal':['atelier'],'atelier':['signal']}).get(actor,[])
                            peer=value.get('to') if isinstance(value,dict) else None
                            if peer not in peers or peer not in cfg['instances']:
                                raise ValueError('recipient outside authorized collaboration')
                            validate_message(value,peer)
                            target=safe_path(Path(cfg['instances'][peer]),'collaboration/inbox')
                            target.mkdir(parents=True,exist_ok=True)
                            identity=hashlib.sha256(key.encode()).hexdigest()[:24]
                            dest=safe_path(Path(cfg['instances'][peer]),'collaboration/inbox/'+identity+'.json')
                            save(dest,dict(sender=actor,at=time.time(),sha256=digest,message=value))
                            save(safe_path(Path(cfg['instances'][peer]),'collaboration/inbox/index.json'),
                                 dict(updated=time.time(),messages=sorted(p.name for p in target.glob('*.json') if p.name!='index.json')))
                            receipt=dict(status='delivered',sha256=digest,to=peer,at=time.time())
                        else:
                            production=kind=='production-requests'
                            body=proposal(value,production)
                            if sum(k.startswith(actor+':') and x.get('status') in ('sending','sent','send_uncertain') and x.get('at',0)>time.time()-86400 for k,x in ledger.items())>=3:continue
                            email, sender, dotenv = email_settings()
                            # Persist before effect. Unknown outcome is never blindly replayed.
                            receipt=dict(status='sending',sha256=digest,at=time.time())
                            ledger[key]=receipt;save(root/'ledger.json',ledger)
                            try:
                                result=subprocess.run([sys.executable,sender,'send',
                                    '--dotenv',dotenv,'--to',email,'--subject',
                                    'Merit: product launch approval requested' if production else 'Signal: research cost approval requested',
                                    '--text',body,'--idempotency-key',('merit-production-' if production else 'signal-cost-')+hashlib.sha256(key.encode()).hexdigest(),
                                    '--confirm-authorized'],capture_output=True,text=True,timeout=45)
                                if result.returncode:raise RuntimeError('email helper failed; inspect privately')
                                receipt.update(status='sent',provider=json.loads(result.stdout))
                            except Exception:
                                receipt.update(status='send_uncertain',error='operator reconciliation required; no automatic resend')
                        ledger[key]=receipt;save(root/'ledger.json',ledger)
                        save(safe_path(base,'collaboration/receipts/'+hashlib.sha256(key.encode()).hexdigest()[:24]+'.json'),receipt)
                    except (ValueError,OSError) as error:
                        ledger[key]=dict(status='rejected',error=type(error).__name__,at=time.time())
                        save(root/'ledger.json',ledger)
        save(root/'health.json',dict(at=time.time(),processed=len(ledger)))


if __name__=='__main__':tick(Path(sys.argv[1]).resolve())

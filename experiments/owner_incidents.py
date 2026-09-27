"""Operator-authorized, fixed-recipient incident mail; independent of peer relays."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from experiments.studio_relay import RelayConfigError, email_settings, safe_path, read_envelope

ROOT = Path(os.environ.get('CONCORDE_INCIDENTS_ROOT', str(Path.home() / '.local/share/concorde/owner-incidents')))
FIELDS = ('incident_id', 'summary', 'impact', 'evidence', 'attempts', 'requested_action')
NOTE = '''# Owner incident email capability

When an important dependency or capability is broken outside your control,
you may email the owner by writing a JSON file to /instance/owner-incidents/outbox/.
Create the directory if necessary. Exact nonempty string fields:
incident_id (stable lowercase letters/digits/hyphens, at most 80 characters),
summary, impact, evidence, attempts, requested_action (each at most 2000 characters).
Explain the observed blocker, reasonable attempts and the specific help needed.
Do not wait indefinitely on a failed tool or invent success. Continue useful
independent work where possible. Ordinary uncertainty is not an infrastructure incident.

Use one stable incident_id per problem: rewriting files or changing filenames
does not send again. Receipts: /instance/owner-incidents/receipts/<incident_id>.json.
The host checks every minute. Limit: 3 emails per instance per rolling day and
12 across the fleet per rolling day; excess stays queued. Unknown send outcomes
require operator reconciliation, not repeated requests. Provider acceptance is
not proof of delivery or approval. No automatic reminders or reply ingestion.
The owner should respond in Codex chat. No credentials, private communications,
raw context dumps or attachments. Email goes only to the configured owner address;
it grants no permission to spend, publish, contact customers or change scope.
This channel requires the host relay and email provider to work. A stopped
instance cannot diagnose or submit a new request; this is not independent uptime monitoring.
'''


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def register(instance, name, root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path=root/'registry.json'
        registry=json.loads(path.read_text()) if path.exists() else {}
        registry[str(instance.resolve())]=name
        save(path, registry)


def validate(value):
    if not isinstance(value, dict) or set(value)!=set(FIELDS):
        raise ValueError('exact incident fields required')
    if any(not isinstance(value[k],str) or not value[k].strip() or len(value[k])>2000 for k in FIELDS):
        raise ValueError('nonempty strings up to 2000 characters required')
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',value['incident_id']):
        raise ValueError('invalid incident_id')
    return value


def send(name, value, key):
    email, sender, dotenv = email_settings()
    body=('Concorde reports an external blocker. No reply ingestion; respond in Codex chat.\n'
          'This request authorizes no spending or external action.\n\n'+
          '\n\n'.join(k+': '+value[k] for k in FIELDS))
    result=subprocess.run([sys.executable,sender,'send','--dotenv',dotenv,
        '--to',email,'--subject',f'{name}: operator help requested',
        '--text',body,'--idempotency-key','c3-incident-'+key,
        '--confirm-authorized'],capture_output=True,text=True,timeout=45)
    if result.returncode: raise RuntimeError('email helper failed')
    return json.loads(result.stdout)


def tick(root=ROOT):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        registry=json.loads((root/'registry.json').read_text())
        path=root/'ledger.json'
        ledger=json.loads(path.read_text()) if path.exists() else {}
        errors=[]
        for location,name in registry.items():
            base=Path(location)
            try:
                state=json.loads((base/'.concorde2/state.json').read_text())
                if state['mode']=='frozen': continue
                folder=safe_path(base,'owner-incidents/outbox')
                # Sent envelopes remain as evidence; they must not permanently
                # occupy a first-N window and starve newer incident requests.
                for file in sorted(folder.glob('*.json')):
                    try:
                        value,digest=read_envelope(safe_path(base,str(file.relative_to(base))))
                        validate(value)
                        key=hashlib.sha256((location+':'+value['incident_id']).encode()).hexdigest()
                        receipt=safe_path(base,'owner-incidents/receipts/'+value['incident_id']+'.json')
                        if key in ledger:
                            save(receipt,ledger[key]); continue
                        recent=[r for r in ledger.values() if r['at']>time.time()-86400]
                        if len(recent)>=12 or sum(r['instance']==location for r in recent)>=3:
                            save(receipt,dict(status='queued',reason='daily rate limit')); continue
                        record=dict(status='sending',at=time.time(),instance=location,sha256=digest)
                        ledger[key]=record;save(path,ledger)
                        try:
                            record.update(status='accepted',provider=send(name,value,key))
                        except RelayConfigError:
                            # No provider call was attempted; retry once configuration is supplied.
                            del ledger[key];save(path,ledger)
                            raise
                        except Exception:
                            record.update(status='send_uncertain',reason='operator reconciliation required; no automatic retry')
                        save(path,ledger);save(receipt,record)
                    except (ValueError,OSError):
                        errors.append(dict(instance=location,file=file.name,error='invalid or inaccessible envelope'))
            except (ValueError,OSError,KeyError):
                errors.append(dict(instance=location,error='instance unavailable'))
        save(root/'health.json',dict(at=time.time(),registered=len(registry),errors=errors))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='register': register(Path(sys.argv[2]),sys.argv[3])
    else: tick()

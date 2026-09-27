"""Explicit, receipt-backed static preview publication. No instance root is served."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import time
from experiments.owner_incidents import save

ROOT=Path(os.environ.get('CONCORDE_PUBLIC_SITES_ROOT', str(Path.home() / '.local/share/concorde/public-sites')))
SUFFIXES={'.html','.css','.js','.json','.svg','.png','.jpg','.jpeg','.webp','.ico','.woff','.woff2','.txt','.md'}
SECRET=re.compile(rb'(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-proj-[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16}|github_pat_[A-Za-z0-9_]{30,}|re_[A-Za-z0-9]{30,})')
PAYMENT=re.compile(rb'https?://(?:buy\.stripe\.com|checkout\.stripe\.com|(?:www\.)?paypal\.(?:com|me)|pay\.link)(?:[/:?]|$)',re.I)
NOTE='''# Public website preview capability

The owner authorizes PUBLIC VIEWING of your website, with checkout disabled.
This specifically supersedes older private-only/public-hosting prohibitions for
this static preview channel. No payments, payment links, purchase/order collection,
accounts, spending, outreach campaigns or real service/support promises. Prices
may be displayed; mark proposals appropriately. Fictional demos stay labeled.
Merit's per-product approval remains necessary for endorsements/affiliate promotion.
No credentials, private correspondence, customer data, internal notes or runtime
files may be published. Review copy for a public audience; remove obsolete claims
that the site is unpublished. The owner authorized viewability, not readiness.

Your URL: URL_PLACEHOLDER
Create /instance/public-site/outbox/ and submit one immutable JSON file per release:
{"release_id":"public-v1","checkout_disabled":true,"public_reviewed":true,
 "files":{"index.html":"product/index.html","styles.css":"product/styles.css"}}
Use the actual assets required by your page. Source paths are instance-relative
under product/ or public-site/; destinations are relative to the public root.
Only explicitly listed static files are copied. No symlinks, hidden paths, servers
or dependency folders. At most 200 files, 5 MB each and 25 MB total. Include
index.html. Retained exports are capped at 256 MB per site; ask the owner for
archival help if needed. Your booleans attest actual review, not a substitute for doing it.
Known payment URLs and common credential patterns are rejected, but this is not
a complete semantic safety detector. Keep all checkout actions disabled yourself.

The host checks each minute and atomically publishes immutable snapshots.
Receipts: /instance/public-site/receipts/<release_id>.json. published means the
release was installed, not independently verified by a human or evidence of demand.
If a receipt is rejected, fix it and use a NEW release_id. Never rewrite a sent ID.
The host serves GET/HEAD only, blocks form submission and third-party connections
via browser policy, and has no payment/order backend. Local demonstration state
is fine. Package dependencies locally; external fonts/scripts won't load. Test
the deployed page, not only a local source. HTML/source links can still navigate
away, so do not add checkout destinations or affiliate links without approval.
For the configured HTTPS preview names, the isolated transport permits a pinned
route to the public server. Python urllib uses the existing HTTPS_PROXY:
python3 -c 'import urllib.request; r=urllib.request.urlopen("URL_PLACEHOLDER", timeout=15); print(r.status, r.url, r.headers.get("Content-Type"))'
For rendered checks, launch playwright-core Chromium with
proxy={server:process.env.HTTPS_PROXY}; Chromium does not automatically use the
shell proxy. Use NODE_PATH=/opt/ui/node_modules and the writable XDG_CONFIG_HOME
and XDG_CACHE_HOME from the activation environment. A successful GET/render is
delivery evidence, not a human UX review or proof of demand.
TLS preview addresses use a temporary shared DNS service. Pages request no search
indexing; that is not access control. Anyone with the URL can view and share them.
Last published snapshots persist after your finite cognition window; do not promise
active support then. A later owner decision can withdraw them. Ordinary workspace
edits do not publish themselves. No need to wait for operator release approval
again within this exact scope. Report environmental blockers via owner incidents.
'''


def parts(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,180}',name):
        raise ValueError('safe relative path required')
    p=PurePosixPath(name)
    if str(p)!=name or p.is_absolute() or any(x.startswith('.') or x in ('node_modules',) for x in p.parts):
        raise ValueError('hidden, parent and dependency paths forbidden')
    if p.suffix.lower() not in SUFFIXES: raise ValueError('not an allowed static asset')
    return p.parts


def read_asset(base,name,limit=5_000_000):
    components=parts(name)
    if components[0] not in ('product','public-site'): raise ValueError('source must be in product/ or public-site/')
    fd=os.open(base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for component in components[:-1]:
            child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        child=os.open(components[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(child,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('regular file required')
            raw=stream.read(limit+1)
        if len(raw)>limit: raise ValueError('file exceeds size limit')
        if SECRET.search(raw): raise ValueError('possible credential in asset')
        if PAYMENT.search(raw): raise ValueError('payment destination in asset')
        return raw
    finally: os.close(fd)


def prepare(base,value):
    if not isinstance(value,dict) or set(value)!={'release_id','checkout_disabled','public_reviewed','files'}:
        raise ValueError('exact release fields required')
    rid=value['release_id']
    if not isinstance(rid,str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',rid): raise ValueError('invalid release_id')
    if value['checkout_disabled'] is not True or value['public_reviewed'] is not True:
        raise ValueError('public review and disabled checkout required')
    files=value['files']
    if not isinstance(files,dict) or not 1<=len(files)<=200 or 'index.html' not in files:
        raise ValueError('index.html and bounded explicit asset map required')
    assets={}
    for target,source in files.items():
        parts(target)
        assets[target]=read_asset(base,source)
    if sum(map(len,assets.values()))>25_000_000: raise ValueError('release exceeds total size limit')
    return assets


def publish(root,actor,value,config):
    rid=value.get('release_id','') if isinstance(value,dict) else ''
    if not isinstance(rid,str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',rid): raise ValueError('invalid release_id')
    directory=root/'www'/actor
    releases=directory/'releases';releases.mkdir(parents=True,exist_ok=True)
    intent=root/'intents'/actor/(rid+'.json')
    if intent.exists():
        # A retry needs neither the original source nor a second publication decision.
        record=json.loads(intent.read_text());digest=record['sha256']
    else:
        assets=prepare(Path(config['instance']),value)
        digest=hashlib.sha256(json.dumps({p:hashlib.sha256(b).hexdigest() for p,b in assets.items()},sort_keys=True).encode()).hexdigest()
        final=releases/digest
        if not final.exists():
            used=sum(f.stat().st_size for f in releases.rglob('*') if f.is_file())
            if used+sum(map(len,assets.values()))>256_000_000: raise ValueError('retained release storage limit reached')
            stage=Path(tempfile.mkdtemp(prefix='.stage-',dir=releases))
            for path,raw in assets.items():
                dest=stage/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
                dest.chmod(0o644)
            stage.chmod(0o755);stage.rename(final)
        record=dict(status='published',at=time.time(),release_id=rid,sha256=digest,
                    files={p:hashlib.sha256(b).hexdigest() for p,b in assets.items()},url=config['url'])
        save(intent,record)
    temporary=directory/'.current-next'
    if temporary.is_symlink(): temporary.unlink()
    temporary.symlink_to('releases/'+digest,target_is_directory=True)
    temporary.replace(directory/'current')
    return record


def tick(root=ROOT):
    from experiments.studio_relay import safe_path,read_envelope
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cfg=json.loads((root/'config.json').read_text())
        ledger=json.loads((root/'ledger.json').read_text()) if (root/'ledger.json').exists() else {}
        errors=[]
        for actor,config in cfg.items():
            base=Path(config['instance'])
            try:
                if json.loads((base/'.concorde2/state.json').read_text())['mode']=='frozen': continue
                folder=safe_path(base,'public-site/outbox')
                for file in sorted(folder.glob('*.json')):
                    key=actor+':'+file.name
                    if key in ledger: continue
                    receipt_name='invalid-'+hashlib.sha256(file.name.encode()).hexdigest()[:20]
                    try:
                        value,_=read_envelope(safe_path(base,str(file.relative_to(base))))
                        rid=value.get('release_id','') if isinstance(value,dict) else ''
                        if isinstance(rid,str) and re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',rid): receipt_name=rid
                        canonical=actor+':id:'+receipt_name
                        if canonical in ledger: record=ledger[canonical]
                        else:
                            record=publish(root,actor,value,config)
                            ledger[canonical]=record
                    except (ValueError,OSError):
                        record=dict(status='rejected',at=time.time(),reason='Invalid manifest, unsafe/missing asset, credential/payment pattern or size limit. Review PUBLIC_SITE.md.')
                    ledger[key]=record;save(root/'ledger.json',ledger)
                    save(safe_path(base,'public-site/receipts/'+receipt_name+'.json'),record)
            except (ValueError,OSError,KeyError): errors.append(actor)
        save(root/'health.json',dict(at=time.time(),instance_errors=errors))


if __name__=='__main__': tick()

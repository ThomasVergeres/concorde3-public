"""Operator setup; all secrets go to private files, never shell arguments."""
import argparse
import json
import os
from pathlib import Path

from .store import Store, uid, digest


def private_write(path, value):
    p = Path(path)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value,f,indent=2)
        f.flush()
        os.fsync(f.fileno())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',required=True)
    p.add_argument('--key-file')
    sub = p.add_subparsers(dest='command',required=True)
    setup = sub.add_parser('setup')
    setup.add_argument('--origin',required=True)
    setup.add_argument('--email',required=True)
    setup.add_argument('--dotenv',help='Read only Resend settings; never copy unrelated keys')
    setup.add_argument('--output',required=True)
    add = sub.add_parser('register')
    add.add_argument('--name',required=True)
    add.add_argument('--instance-id')
    add.add_argument('--origin',required=True)
    add.add_argument('--output',required=True)
    boot = sub.add_parser('enrollment-link')
    boot.add_argument('--origin',required=True)
    boot.add_argument('--output',required=True)
    boot.add_argument('--recover',action='store_true')
    sub.add_parser('status')
    disable=sub.add_parser('disable');disable.add_argument('--instance-id',required=True)
    rotate=sub.add_parser('rotate');rotate.add_argument('--instance-id',required=True)
    rotate.add_argument('--origin',required=True);rotate.add_argument('--output',required=True)
    args = p.parse_args()
    os.umask(0o077)
    s = Store(args.data,args.key_file)
    if args.command=='setup':
        from urllib.parse import urlsplit
        u=urlsplit(args.origin)
        if u.scheme!='https' or not u.hostname or u.path or u.username or u.query or u.fragment:
            p.error('fixed HTTPS origin required')
        values={}
        if args.dotenv:
            for line in Path(args.dotenv).read_text().splitlines():
                line=line.strip().removeprefix('export ')
                if '=' not in line:continue
                key,value=line.split('=',1)
                if key.strip() in ('RESEND_API_KEY','RESEND_FROM_EMAIL','RESEND_RECEIVING_DOMAIN'):
                    values[key.strip()]=value.strip().strip('\"\'')
        sender=values.get('RESEND_FROM_EMAIL') or ('Concorde <codex@'+values.get('RESEND_RECEIVING_DOMAIN','')+'>')
        if not values.get('RESEND_API_KEY') or not values.get('RESEND_FROM_EMAIL',values.get('RESEND_RECEIVING_DOMAIN')):
            p.error('Resend key and verified sender/domain are required')
        private_write(args.output,{'data':str(Path(args.data).resolve()),'origin':args.origin,'email':args.email,
                                  'key_file':args.key_file,'sender':sender,'resend_key':values['RESEND_API_KEY']})
        print(json.dumps({'configuration_file':args.output,'origin':args.origin,'email_enabled':True}))
    elif args.command=='register':
        if Path(args.output).exists():
            p.error('output already exists; refusing to replace credentials')
        ident, token = s.add_instance(args.name,args.instance_id)
        private_write(args.output, {'origin':args.origin,'instance':ident,'token':token})
        print(json.dumps({'instance':ident,'credentials_file':args.output}))
    elif args.command=='enrollment-link':
        with s.db() as c:
            exists = c.execute('SELECT count(*) FROM passkeys').fetchone()[0]>0
        if exists and not args.recover:
            p.error('owner already enrolled; explicit --recover required')
        token=s.link('recover' if exists else 'enroll')
        private_write(args.output, {'url':args.origin+'/#setup='+token})
        print(json.dumps({'private_link_file':args.output,'expires_seconds':900}))
    elif args.command in ('disable','rotate'):
        with s.db() as c:
            if not c.execute('SELECT 1 FROM instances WHERE id=?',(args.instance_id,)).fetchone():p.error('unknown instance')
            if args.command=='disable':
                c.execute('UPDATE instances SET enabled=0 WHERE id=?',(args.instance_id,))
            else:
                token=uid()
                private_write(args.output,{'origin':args.origin,'instance':args.instance_id,'token':token})
                c.execute('UPDATE instances SET token_hash=?,enabled=1 WHERE id=?',(digest(token),args.instance_id))
            s.audit(c,'instance_'+args.command,args.instance_id)
        print(json.dumps({'instance':args.instance_id,'operation':args.command}))
    else:
        with s.db() as c:
            print(json.dumps({'instances':[dict(r) for r in c.execute('SELECT id,name,enabled FROM instances')],
                'requests':[dict(r) for r in c.execute('SELECT state,count(*) AS count FROM requests GROUP BY state')],
                'passkeys':c.execute('SELECT count(*) FROM passkeys').fetchone()[0],
                'email':[dict(r) for r in c.execute('SELECT state,count(*) AS count FROM outbox GROUP BY state')]}))


if __name__=='__main__':
    main()

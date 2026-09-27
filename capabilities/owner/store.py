"""Transactional capability state. No graph authority or model invocation."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def uid():
    return secrets.token_urlsafe(24)


class Fault(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Store:
    def __init__(self, directory, key_file=None, clock=time.time):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o077:
            raise ValueError("runtime directory must have mode 0700")
        self.path = self.root / "owner.sqlite3"
        self.clock = clock
        key_path = Path(key_file) if key_file else self.root / "vault.key"
        if not key_path.exists():
            if self.path.exists():
                raise ValueError("existing database requires its original vault key")
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(AESGCM.generate_key(bit_length=256))
                f.flush()
                os.fsync(f.fileno())
        if key_path.is_symlink() or not stat.S_ISREG(key_path.stat().st_mode) or key_path.stat().st_mode & 0o077:
            raise ValueError("vault key must be a private regular file")
        self.aes = AESGCM(key_path.read_bytes())
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS instances(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, instance TEXT NOT NULL,
                idem TEXT NOT NULL, body TEXT NOT NULL, scope TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, deadline REAL NOT NULL, response TEXT NOT NULL DEFAULT '{}',
                response_key TEXT, verification TEXT, UNIQUE(instance,idem));
            CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY, instance TEXT NOT NULL,
                request TEXT NOT NULL, ciphertext BLOB NOT NULL, expires REAL NOT NULL,
                single_use INTEGER NOT NULL, claim_hash TEXT, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL, request TEXT NOT NULL, kind TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL, subject TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, csrf TEXT NOT NULL,
                level TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY, purpose TEXT NOT NULL,
                session TEXT, value BLOB NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS passkeys(id TEXT PRIMARY KEY, public_key BLOB NOT NULL,
                counter INTEGER NOT NULL, label TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS links(hash TEXT PRIMARY KEY, purpose TEXT NOT NULL,
                expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS rate(key TEXT PRIMARY KEY, count INTEGER NOT NULL,
                expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, payload BLOB NOT NULL,
                created REAL NOT NULL, next_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'pending', receipt TEXT);
            CREATE TABLE IF NOT EXISTS dependencies(id TEXT NOT NULL, instance TEXT NOT NULL,
                revision INTEGER NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(instance,id));
            CREATE TABLE IF NOT EXISTS billing(id TEXT PRIMARY KEY, instance TEXT NOT NULL,
                idem TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL,
                UNIQUE(instance,idem));
            CREATE TABLE IF NOT EXISTS dependency_observations(instance TEXT NOT NULL,
                id TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(instance,id,revision));
            ''')
            check = c.execute("SELECT value FROM challenges WHERE id='key-check'").fetchone()
            if check:
                self.decrypt(check[0], "key-check")
            else:
                c.execute("INSERT INTO challenges VALUES('key-check','internal',NULL,?,0)",
                          (self.encrypt(b"owner-capability-v1", "key-check"),))
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA secure_delete=ON")
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def encrypt(self, value, context):
        nonce = secrets.token_bytes(12)
        return nonce + self.aes.encrypt(nonce, value, context.encode())

    def decrypt(self, value, context):
        return self.aes.decrypt(value[:12], value[12:], context.encode())

    def audit(self, c, kind, subject):
        c.execute("INSERT INTO audit(kind,subject,at) VALUES(?,?,?)", (kind, subject, self.clock()))

    def event(self, c, row, kind):
        c.execute("INSERT INTO events(instance,request,kind,at) VALUES(?,?,?,?)",
                  (row['instance'], row['id'], kind, self.clock()))
        self.audit(c, kind, row['id'])

    def rate(self, key, limit, seconds):
        with self.db() as c:
            now = self.clock()
            c.execute("DELETE FROM rate WHERE expires < ?", (now,))
            c.execute("INSERT INTO rate VALUES(?,0,?) ON CONFLICT(key) DO NOTHING", (key, now+seconds))
            c.execute("UPDATE rate SET count=count+1 WHERE key=?", (key,))
            count = c.execute("SELECT count FROM rate WHERE key=?", (key,)).fetchone()[0]
        if count > limit:
            raise Fault(429, "Too many attempts. Please wait before trying again.")

    def add_instance(self, name, instance=None):
        ident, token = instance or uid(), uid()+uid()
        with self.db() as c:
            c.execute("INSERT INTO instances(id,name,token_hash) VALUES(?,?,?)", (ident, name, digest(token)))
            self.audit(c, "instance_registered", ident)
        return ident, token

    def instance(self, token):
        with self.db() as c:
            row = c.execute("SELECT id FROM instances WHERE token_hash=? AND enabled=1", (digest(token),)).fetchone()
        if not row:
            raise Fault(401, "Instance authentication required")
        return row[0]

    def expire(self, c):
        rows = c.execute("SELECT * FROM requests WHERE state='pending' AND deadline<=?", (self.clock(),)).fetchall()
        for row in rows:
            c.execute("UPDATE requests SET state='expired' WHERE id=?", (row['id'],))
            self.event(c, row, "expired")
        c.execute("DELETE FROM sessions WHERE expires<=?", (self.clock(),))
        c.execute("DELETE FROM challenges WHERE id!='key-check' AND expires<=?", (self.clock(),))
        c.execute("DELETE FROM links WHERE expires<=?", (self.clock(),))
        # Crypto erasure at expiry/revocation is best-effort for historical backups.
        c.execute("UPDATE credentials SET ciphertext=X'' WHERE expires<=? OR revoked=1", (self.clock(),))
        for dep in c.execute("SELECT * FROM dependencies WHERE (instance,id,revision) NOT IN (SELECT instance,id,revision FROM dependency_observations)").fetchall():
            body=json.loads(dep['body'])
            if body.get('next_review') is not None and body['next_review']<=self.clock() and body['state']!='cancelled':
                c.execute('INSERT INTO dependency_observations VALUES(?,?,?)',(dep['instance'],dep['id'],dep['revision']))
                self.event(c,dep,'dependency_review')

    def project(self, row):
        body = json.loads(row['body'])
        return dict(body, id=row['id'], instance=row['instance'], scope_text=body['scope'], scope=row['scope'], state=row['state'],
                    created=row['created'], response=json.loads(row['response']),
                    verification=json.loads(row['verification']) if row['verification'] else None)

    def get_row(self, c, ident, instance=None):
        row = c.execute("SELECT * FROM requests WHERE id=?", (ident,)).fetchone()
        if not row or (instance and row['instance'] != instance):
            raise Fault(404, "Request not found")
        return row

    def create(self, instance, body):
        raw = packed(body)
        with self.db() as c:
            self.expire(c)
            old = c.execute("SELECT * FROM requests WHERE instance=? AND idem=?", (instance, body['key'])).fetchone()
            if old:
                if old['body'] != raw:
                    raise Fault(409, "Idempotency key already identifies different terms. Create a new request.")
                return self.project(old)
            if not self.clock() < body['deadline'] <= self.clock()+90*86400:
                raise Fault(422, "Deadline must be in the next 90 days")
            if c.execute("SELECT count(*) FROM requests WHERE instance=? AND state='pending'", (instance,)).fetchone()[0] >= 50:
                raise Fault(409, "Pending request limit reached; resolve or cancel obsolete requests")
            ident = uid()
            c.execute("INSERT INTO requests(id,instance,idem,body,scope,state,created,deadline) VALUES(?,?,?,?,?,'pending',?,?)",
                      (ident, instance, body['key'], raw, digest(raw), self.clock(), body['deadline']))
            row = self.get_row(c, ident)
            self.event(c, row, "created")
            self.queue_mail(c, "request:"+ident, {"kind": "request", "request": ident})
            return self.project(row)

    def get(self, ident, instance=None):
        with self.db() as c:
            self.expire(c)
            return self.project(self.get_row(c, ident, instance))

    def listing(self, instance=None, offset=0, limit=30):
        with self.db() as c:
            self.expire(c)
            rows = c.execute("SELECT r.*, i.name AS instance_name FROM requests r JOIN instances i ON i.id=r.instance "
                             "WHERE (? IS NULL OR r.instance=?) ORDER BY (r.state='pending') DESC,r.created DESC LIMIT ? OFFSET ?",
                             (instance, instance, limit+1, offset)).fetchall()
            return {"items": [dict(self.project(r), instance_name=r['instance_name']) for r in rows[:limit]],
                    "next_offset": offset+limit if len(rows)>limit else None}

    def respond(self, ident, data):
        with self.db() as c:
            self.expire(c)
            row = self.get_row(c, ident)
            if data['scope'] != row['scope']:
                raise Fault(409, "Request terms changed. Reload before responding.")
            # Replayed submission returns the committed result without echoing a secret.
            if row['response_key'] == data['key']:
                return self.project(row)
            if row['state'] != 'pending':
                raise Fault(409, "This request no longer accepts responses")
            body = json.loads(row['body'])
            action = data['action']
            expected = {'decision': 'approve', 'answer': 'answer', 'credential': 'supply', 'external': 'done'}[body['kind']]
            if action not in (expected, 'decline') and not (action=='propose' and body['kind']=='decision'):
                raise Fault(422, "Action does not match this request")
            response = {"action": action, "at": self.clock(), "note": data.get('note', '')}
            if action == 'supply':
                value = data.get('secret', '')
                if not value or len(value.encode()) > 1_400_000:
                    raise Fault(422, "Supply a credential or file within the displayed limit")
                secret_id = uid()
                expires = min(self.clock()+body['secret_ttl'], self.clock()+365*86400)
                payload = packed({"value": value, "filename": data.get('filename', ''), "encoding": data.get('encoding', 'text')})
                c.execute("INSERT INTO credentials(id,instance,request,ciphertext,expires,single_use) VALUES(?,?,?,?,?,?)",
                          (secret_id, row['instance'], ident,
                           self.encrypt(payload.encode(), f"{row['instance']}:{ident}:{secret_id}"), expires, body['single_use']))
                response.update(credential=secret_id, expires=expires)
                # Secret handoff has no free text reflection of the entered value.
                response.pop('note', None)
            if action in ('answer','propose') and not response['note'].strip():
                raise Fault(422, "Enter an answer")
            state = 'declined' if action == 'decline' else 'proposed' if action=='propose' else 'supplied'
            c.execute("UPDATE requests SET state=?,response=?,response_key=? WHERE id=?", (state, packed(response), data['key'], ident))
            self.event(c, row, state)
            return self.project(self.get_row(c, ident))

    def cancel(self, instance, ident):
        with self.db() as c:
            self.expire(c)
            row = self.get_row(c, ident, instance)
            if row['state'] == 'cancelled':
                return self.project(row)
            if row['state'] != 'pending':
                raise Fault(409, "Only pending requests can be cancelled")
            c.execute("UPDATE requests SET state='cancelled' WHERE id=?", (ident,))
            self.event(c, row, 'cancelled')
            return self.project(self.get_row(c, ident))

    def verify(self, instance, ident, ok, evidence):
        with self.db() as c:
            row = self.get_row(c, ident, instance)
            value = packed({"ok": ok, "evidence": evidence})
            if row['verification'] == value:
                return self.project(row)
            if row['state'] != 'supplied':
                raise Fault(409, "Verification requires a supplied response; prior verification is immutable")
            state = 'verified' if ok else 'failed'
            c.execute("UPDATE requests SET state=?,verification=? WHERE id=?", (state, value, ident))
            self.event(c, row, state)
            return self.project(self.get_row(c, ident))

    def secret(self, instance, ident, claim):
        with self.db() as c:
            self.expire(c)
            row = c.execute("SELECT * FROM credentials WHERE id=? AND instance=?", (ident, instance)).fetchone()
            if not row:
                raise Fault(404, "Credential not found")
            if row['revoked'] or row['expires'] <= self.clock():
                raise Fault(410, "Credential expired or revoked; request a replacement if needed")
            if row['single_use']:
                if not claim:
                    raise Fault(422, "Single-use retrieval requires a stable claim key")
                if row['claim_hash'] and row['claim_hash'] != digest(claim):
                    raise Fault(409, "Credential already claimed by another retrieval")
                c.execute("UPDATE credentials SET claim_hash=? WHERE id=?", (digest(claim), ident))
            self.audit(c, "credential_read", ident)
            return json.loads(self.decrypt(row['ciphertext'], f"{instance}:{row['request']}:{ident}"))

    def revoke(self, instance, ident):
        with self.db() as c:
            row = c.execute("SELECT * FROM credentials WHERE id=? AND (? IS NULL OR instance=?)", (ident, instance, instance)).fetchone()
            if not row:
                raise Fault(404, "Credential not found")
            c.execute("UPDATE credentials SET revoked=1,ciphertext=X'' WHERE id=?", (ident,))
            self.audit(c, "credential_revoked", ident)
            self.event(c, self.get_row(c, row['request']), 'credential_revoked')
        return {"revoked": True}

    def events(self, instance, after=0):
        with self.db() as c:
            self.expire(c)
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE instance=? AND seq>? ORDER BY seq LIMIT 100", (instance, after))]

    def queue_mail(self, c, ident, payload):
        c.execute("INSERT OR IGNORE INTO outbox(id,payload,created,next_at) VALUES(?,?,?,?)",
                  (ident, self.encrypt(packed(payload).encode(), 'mail:'+ident), self.clock(), self.clock()))

    def link(self, purpose):
        raw = uid()+uid()
        with self.db() as c:
            c.execute("INSERT INTO links VALUES(?,?,?)", (digest(raw), purpose, self.clock()+900))
        return raw

    def session(self, level, c=None):
        raw, csrf = uid()+uid(), uid()
        def create(conn):
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (digest(raw), csrf, level, self.clock()+(43200 if level=='owner' else 900), self.clock()))
        if c is not None:
            create(c)
        else:
            with self.db() as conn:
                create(conn)
        return raw, csrf

    def redeem(self, token):
        with self.db() as c:
            row = c.execute("SELECT * FROM links WHERE hash=? AND expires>?", (digest(token), self.clock())).fetchone()
            if not row:
                raise Fault(401, "This link has expired or was already used. Request a new one.")
            c.execute("DELETE FROM links WHERE hash=?", (digest(token),))
            return self.session(row['purpose'], c)

    def authenticate(self, token, csrf=None, owner=True):
        with self.db() as c:
            row = c.execute("SELECT * FROM sessions WHERE hash=? AND expires>?", (digest(token), self.clock())).fetchone()
        if not row or (owner and row['level'] != 'owner'):
            raise Fault(401, "Sign in with your passkey to continue")
        if csrf is not None and not secrets.compare_digest(row['csrf'], csrf):
            raise Fault(403, "Session verification failed. Reload and try again.")
        return dict(row)

    def dependency(self, instance, ident, revision, body):
        with self.db() as c:
            if body.get('credential_ref') and not c.execute('SELECT 1 FROM credentials WHERE id=? AND instance=?',(body['credential_ref'],instance)).fetchone():
                raise Fault(404, 'Credential reference not found for this instance')
            old = c.execute("SELECT * FROM dependencies WHERE instance=? AND id=?", (instance, ident)).fetchone()
            if (old['revision'] if old else 0) != revision:
                raise Fault(409, "Dependency changed; read its current revision before updating")
            c.execute("INSERT INTO dependencies VALUES(?,?,?,?,?) ON CONFLICT(instance,id) DO UPDATE SET revision=excluded.revision,body=excluded.body,updated=excluded.updated",
                      (ident, instance, revision+1, packed(body), self.clock()))
            self.audit(c, 'dependency_updated', instance+':'+ident)
        return dict(body, id=ident, revision=revision+1)

    def bill(self, instance, body):
        with self.db() as c:
            old = c.execute("SELECT * FROM billing WHERE instance=? AND idem=?", (instance, body['key'])).fetchone()
            if old:
                if old['body'] != packed(body):
                    raise Fault(409, "Billing idempotency key conflicts with its original receipt")
                return dict(body, id=old['id'])
            ident = uid()
            c.execute("INSERT INTO billing VALUES(?,?,?,?,?)", (ident, instance, body['key'], packed(body), self.clock()))
            self.audit(c, 'billing_recorded', ident)
            return dict(body, id=ident)

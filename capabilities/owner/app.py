"""Same-origin mobile portal and instance-scoped capability API."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from webauthn import (generate_registration_options, generate_authentication_options,
                      verify_registration_response, verify_authentication_response)
from webauthn.helpers import options_to_json, base64url_to_bytes
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, ResidentKeyRequirement,
                                    UserVerificationRequirement, PublicKeyCredentialDescriptor)

from .store import Store, Fault, digest, packed, uid


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class NewRequest(Input):
    key: str = Field(min_length=8, max_length=160)
    kind: Literal['decision', 'answer', 'credential', 'external']
    title: str = Field(min_length=1, max_length=140)
    reason: str = Field(min_length=1, max_length=3000)
    scope: str = Field(min_length=1, max_length=3000)
    fallback: str = Field(min_length=1, max_length=1600)
    deadline: float
    action_url: str = Field(default='', max_length=2048)
    secret_ttl: int = Field(default=86400*30, ge=30, le=86400*365)
    single_use: bool = False


class Reply(Input):
    key: str = Field(min_length=8, max_length=160)
    scope: str = Field(min_length=64, max_length=64)
    action: Literal['approve', 'decline', 'propose', 'answer', 'supply', 'done']
    note: str = Field(default='', max_length=6000)
    secret: str = Field(default='', max_length=1_400_000)
    filename: str = Field(default='', max_length=180)
    encoding: Literal['text', 'base64'] = 'text'


class Verify(Input):
    ok: bool
    evidence: str = Field(min_length=1, max_length=3000)


class Claim(Input):
    key: str = Field(default='', max_length=160)


class Token(Input):
    token: str = Field(min_length=16, max_length=256)


class Ceremony(Input):
    challenge_id: str = Field(max_length=128)
    credential: dict
    label: str = Field(default='My passkey', min_length=1, max_length=80)


class Dependency(Input):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=3000)
    account_ref: str = Field(default='', max_length=300)
    credential_ref: str = Field(default='', max_length=160)
    state: Literal['considering', 'purchased', 'awaiting_enablement', 'working', 'degraded', 'cancelled']
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    recurring_minor: int = Field(ge=0, le=10**12)
    period: Literal['none', 'month', 'year', 'usage']
    next_review: float | None = None
    cancellation: str = Field(default='', max_length=2000)
    commitments: list[str] = Field(default_factory=list, max_length=30)
    request_ids: list[str] = Field(default_factory=list, max_length=30)
    evidence: str = Field(min_length=1, max_length=3000)


class Bill(Input):
    key: str = Field(min_length=8, max_length=160)
    dependency: str = Field(min_length=1, max_length=160)
    provider_ref: str = Field(min_length=1, max_length=300)
    kind: Literal['invoice', 'payment', 'refund', 'credit', 'commitment', 'cancellation']
    amount_minor: int = Field(ge=0, le=10**12)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    evidence: str = Field(min_length=1, max_length=3000)


def create_app(store: Store, origin: str, email: str, notifier=None, *, development=False):
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' and not (development and parsed.hostname in ('localhost', '127.0.0.1', 'testserver'))) or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('A fixed HTTPS origin is required (no path or user info)')
    origin = origin.rstrip('/')
    rp = parsed.hostname
    cookie_name = 'owner_session' if development else '__Host-owner_session'
    web = Path(__file__).parent / 'web'
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if notifier:
            thread = threading.Thread(target=notifier.run, args=(stop,), daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=12)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store

    @app.exception_handler(Fault)
    async def fault(_, exc):
        return JSONResponse({'error': exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation(_, exc):
        # Pydantic's default includes raw submitted values, including secrets.
        return JSONResponse({'error': 'Invalid input. Check the fields and limits.',
                             'fields': ['.'.join(map(str, e['loc'])) for e in exc.errors()]}, status_code=422)

    @app.middleware('http')
    async def boundary(request, next_handler):
        headers = {
            'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
        }
        if not development:
            headers['Strict-Transport-Security'] = 'max-age=31536000'
        try:
            if request.headers.get('host') != parsed.netloc:
                raise Fault(400, 'Unexpected host')
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                # API clients use bearer auth; browser mutations must be same-origin.
                if not request.url.path.startswith('/v1/') and request.headers.get('origin') != origin:
                    raise Fault(403, 'Open this page directly to continue')
                if request.headers.get('origin') and request.headers['origin'] != origin:
                    raise Fault(403, 'Cross-origin operation rejected')
                length = request.headers.get('content-length', '0')
                if not length.isdigit() or int(length) > 1_600_000:
                    raise Fault(413, 'Submission too large (maximum file size 1 MiB)')
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 1_600_000:
                        raise Fault(413, 'Submission too large')
                request._body = bytes(body)
            ip = request.client.host if request.client else 'unknown'
            # No trust in client-supplied forwarding headers; configure the proxy explicitly.
            if request.url.path.startswith('/auth/'):
                store.rate('auth:'+ip, 100, 600)
            response = await next_handler(request)
        except Fault as exc:
            response = JSONResponse({'error': exc.message}, status_code=exc.status)
        except Exception:
            # Do not serialize exception details or request data to logs/responses.
            response = JSONResponse({'error': 'The service could not complete this action. Please retry with the same request.'}, status_code=500)
        response.headers.update(headers)
        return response

    def session(request, owner=True, mutate=False):
        token = request.cookies.get(cookie_name, '')
        return store.authenticate(token, request.headers.get('x-csrf-token', '') if mutate else None, owner)

    def instance(request):
        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer '):
            raise Fault(401, 'Instance authentication required')
        ident = store.instance(auth[7:])
        store.rate('instance:'+ident, 300, 60)
        return ident

    def set_session(raw, csrf, level='owner'):
        result = JSONResponse({'csrf': csrf, 'level': level})
        result.set_cookie(cookie_name, raw, httponly=True, secure=not development,
                          samesite='strict', max_age=43200 if level=='owner' else 900, path='/')
        return result

    def challenge(c, purpose, options, session_hash=None):
        ident = uid()
        c.execute("INSERT INTO challenges VALUES(?,?,?,?,?)", (ident, purpose, session_hash, options.challenge, store.clock()+300))
        return {'challenge_id': ident, 'options': json.loads(options_to_json(options))}

    def take_challenge(ident, purpose, session_hash=None):
        with store.db() as c:
            row = c.execute("SELECT * FROM challenges WHERE id=? AND purpose=? AND expires>?", (ident, purpose, store.clock())).fetchone()
            if not row or row['session'] != session_hash:
                raise Fault(401, 'Authentication expired. Please start again.')
            c.execute("DELETE FROM challenges WHERE id=?", (ident,))
            return row['value']

    @app.get('/')
    def index():
        return FileResponse(web / 'index.html')

    @app.get('/assets/{name}')
    def asset(name):
        if name not in ('app.js', 'style.css', 'mark.svg'):
            raise Fault(404, 'Not found')
        return FileResponse(web / name)

    @app.get('/healthz')
    def health():
        with store.db() as c:
            c.execute('SELECT 1').fetchone()
        return {'ok': True}

    @app.get('/auth/session')
    def current(request: Request):
        value = session(request, owner=False)
        return {'csrf': value['csrf'], 'level': value['level'], 'email': email}

    @app.post('/auth/email')
    def email_link(request: Request):
        store.rate('mail-link-global', 3, 3600)
        if not notifier:
            raise Fault(503, 'Email is not configured. Contact the operator for enrollment.')
        with store.db() as c:
            count = c.execute('SELECT count(*) FROM passkeys').fetchone()[0]
        purpose = 'recover' if count else 'enroll'
        token = store.link(purpose)
        with store.db() as c:
            store.queue_mail(c, 'login:'+uid(), {'kind': 'login', 'token': token, 'purpose': purpose})
        return {'sent': True, 'message': 'A 15-minute setup/recovery link will be sent to the configured owner.'}

    @app.post('/auth/redeem')
    def redeem(data: Token):
        raw, csrf = store.redeem(data.token)
        value = store.authenticate(raw, owner=False)
        return set_session(raw, csrf, value['level'])

    @app.post('/auth/register/options')
    def register_options(request: Request):
        s = session(request, owner=False, mutate=True)
        if s['level'] == 'owner' and store.clock()-s['created'] > 300:
            raise Fault(401, 'Sign in again before adding a passkey')
        with store.db() as c:
            existing = c.execute('SELECT id FROM passkeys').fetchall()
            if s['level'] == 'enroll' and existing:
                raise Fault(409, 'Owner already enrolled. Use sign-in or explicit recovery.')
            options = generate_registration_options(rp_id=rp, rp_name='Concorde', user_name=email,
                user_id=hashlib.sha256(email.encode()).digest(),
                exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r[0])) for r in existing],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED))
            return challenge(c, 'register', options, s['hash'])

    @app.post('/auth/register/verify')
    def register_verify(request: Request, data: Ceremony):
        s = session(request, owner=False, mutate=True)
        expected = take_challenge(data.challenge_id, 'register', s['hash'])
        try:
            verified = verify_registration_response(credential=data.credential, expected_challenge=expected,
                expected_rp_id=rp, expected_origin=origin, require_user_verification=True)
        except Exception:
            raise Fault(401, 'Passkey could not be verified. Please start again.')
        credential_id = base64.urlsafe_b64encode(verified.credential_id).rstrip(b'=').decode()
        with store.db() as c:
            # Recheck under the commit lock, not just before authenticator interaction.
            live = c.execute("SELECT * FROM sessions WHERE hash=? AND expires>?", (s['hash'], store.clock())).fetchone()
            if not live:
                raise Fault(401, 'Enrollment session expired')
            if s['level']=='enroll' and c.execute('SELECT count(*) FROM passkeys').fetchone()[0]:
                raise Fault(409, 'Owner already enrolled')
            if s['level']=='recover':
                c.execute('DELETE FROM passkeys')
                c.execute('DELETE FROM sessions')
                c.execute('DELETE FROM links')
                c.execute("DELETE FROM challenges WHERE id!='key-check'")
                store.audit(c, 'owner_recovered', 'owner')
            c.execute('INSERT INTO passkeys VALUES(?,?,?,?,?)', (credential_id, verified.credential_public_key,
                      verified.sign_count, data.label, store.clock()))
            c.execute('DELETE FROM sessions WHERE hash=?', (s['hash'],))
            raw, csrf = store.session('owner', c)
            store.audit(c, 'passkey_registered', credential_id)
        return set_session(raw, csrf)

    @app.post('/auth/login/options')
    def login_options():
        with store.db() as c:
            store.expire(c)
            options = generate_authentication_options(rp_id=rp, user_verification=UserVerificationRequirement.REQUIRED)
            return challenge(c, 'login', options)

    @app.post('/auth/login/verify')
    def login_verify(data: Ceremony):
        expected = take_challenge(data.challenge_id, 'login')
        ident = data.credential.get('id', '')
        with store.db() as c:
            cred = c.execute('SELECT * FROM passkeys WHERE id=?', (ident,)).fetchone()
            if not cred:
                raise Fault(401, 'Passkey not recognized')
            try:
                v = verify_authentication_response(credential=data.credential, expected_challenge=expected,
                    expected_rp_id=rp, expected_origin=origin, credential_public_key=cred['public_key'],
                    credential_current_sign_count=cred['counter'], require_user_verification=True)
                handle = data.credential.get('response', {}).get('userHandle')
                if handle and base64url_to_bytes(handle) != hashlib.sha256(email.encode()).digest():
                    raise ValueError('wrong user handle')
            except Exception:
                raise Fault(401, 'Passkey could not be verified. Please start again.')
            c.execute('UPDATE passkeys SET counter=? WHERE id=?', (v.new_sign_count, ident))
            raw, csrf = store.session('owner', c)
            store.audit(c, 'owner_signed_in', ident)
        return set_session(raw, csrf)

    @app.post('/auth/logout')
    def logout(request: Request):
        s = session(request, owner=False, mutate=True)
        with store.db() as c:
            c.execute('DELETE FROM sessions WHERE hash=?', (s['hash'],))
        result = JSONResponse({'ok': True})
        result.delete_cookie(cookie_name, path='/', secure=not development, httponly=True, samesite='strict')
        return result

    @app.get('/api/requests')
    def requests(request: Request, offset: int=0):
        session(request)
        return store.listing(offset=max(0, offset))

    @app.get('/api/requests/{ident}')
    def request_detail(ident: str, request: Request):
        session(request)
        result = store.get(ident)
        with store.db() as c:
            result['instance_name'] = c.execute('SELECT name FROM instances WHERE id=?', (result['instance'],)).fetchone()[0]
        return result

    @app.post('/api/requests/{ident}/respond')
    def respond(ident: str, request: Request, data: Reply):
        session(request, mutate=True)
        if data.encoding=='base64' and data.secret:
            try:
                content = base64.b64decode(data.secret, validate=True)
                if len(content)>1024*1024:
                    raise ValueError()
            except Exception:
                raise Fault(422, 'Invalid file, or file exceeds 1 MiB')
        if '/' in data.filename or '\\' in data.filename:
            raise Fault(422, 'Use a file name without a path')
        return store.respond(ident, data.model_dump())

    @app.post('/api/credentials/{ident}/revoke')
    def revoke_owner(ident: str, request: Request):
        session(request, mutate=True)
        return store.revoke(None, ident)

    @app.get('/api/operations')
    def operations(request: Request, offset: int=0):
        session(request)
        with store.db() as c:
            rows = c.execute('SELECT d.*,i.name AS instance_name FROM dependencies d JOIN instances i ON i.id=d.instance ORDER BY d.updated DESC LIMIT 31 OFFSET ?', (max(offset,0),)).fetchall()
        return {'items': [dict(json.loads(r['body']), id=r['id'], instance=r['instance'], instance_name=r['instance_name'], revision=r['revision']) for r in rows[:30]], 'next_offset': offset+30 if len(rows)>30 else None}

    @app.post('/v1/requests')
    def new(request: Request, data: NewRequest):
        ident = instance(request)
        if data.action_url:
            p = urlsplit(data.action_url)
            if p.scheme != 'https' or not p.hostname or p.username or p.password:
                raise Fault(422, 'External action links must use HTTPS without embedded credentials')
        return store.create(ident, data.model_dump())

    @app.get('/v1/requests')
    def own_requests(request: Request, offset: int=0):
        return store.listing(instance(request), max(0,offset))

    @app.get('/v1/requests/{ident}')
    def own_request(ident: str, request: Request):
        return store.get(ident, instance(request))

    @app.post('/v1/requests/{ident}/cancel')
    def cancel(ident: str, request: Request):
        return store.cancel(instance(request), ident)

    @app.post('/v1/requests/{ident}/verify')
    def verify(ident: str, request: Request, data: Verify):
        return store.verify(instance(request), ident, data.ok, data.evidence)

    @app.post('/v1/credentials/{ident}/read')
    def read_secret(ident: str, request: Request, data: Claim):
        return store.secret(instance(request), ident, data.key)

    @app.post('/v1/credentials/{ident}/revoke')
    def revoke(ident: str, request: Request):
        return store.revoke(instance(request), ident)

    @app.get('/v1/events')
    def events(request: Request, after: int=0):
        return {'events': store.events(instance(request), max(0,after))}

    @app.put('/v1/dependencies/{ident}')
    def dependency(ident: str, request: Request, data: Dependency):
        inst = instance(request)
        if len(ident)>160:
            raise Fault(422, 'Dependency ID too long')
        for ref in data.request_ids:
            store.get(ref, inst)
        body = data.model_dump(exclude={'revision'})
        if len(packed(body).encode())>12000:
            raise Fault(422, 'Dependency record exceeds 12 KB')
        return store.dependency(inst, ident, data.revision, body)

    @app.get('/v1/dependencies')
    def dependencies(request: Request, offset: int=0):
        inst = instance(request)
        with store.db() as c:
            rows = c.execute('SELECT * FROM dependencies WHERE instance=? ORDER BY id LIMIT 31 OFFSET ?', (inst,max(offset,0))).fetchall()
        return {'items':[dict(json.loads(r['body']),id=r['id'],revision=r['revision']) for r in rows[:30]], 'next_offset': offset+30 if len(rows)>30 else None}

    @app.post('/v1/billing')
    def billing(request: Request, data: Bill):
        inst = instance(request)
        with store.db() as c:
            if not c.execute('SELECT 1 FROM dependencies WHERE instance=? AND id=?',(inst,data.dependency)).fetchone():
                raise Fault(404, 'Dependency not found')
        return store.bill(inst, data.model_dump())

    @app.get('/v1/billing')
    def bills(request: Request, offset: int=0):
        inst = instance(request)
        with store.db() as c:
            rows = c.execute('SELECT * FROM billing WHERE instance=? ORDER BY created DESC LIMIT 31 OFFSET ?', (inst,max(offset,0))).fetchall()
        return {'items':[dict(json.loads(r['body']),id=r['id']) for r in rows[:30]], 'next_offset': offset+30 if len(rows)>30 else None}

    return app

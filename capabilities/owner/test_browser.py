"""Real WebAuthn verifier + Chromium virtual authenticator; not an iOS claim."""
import os
from pathlib import Path
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from .app import create_app
from .store import Store
from .test_owner import body


@pytest.fixture
def live(tmp_path):
    os.chmod(tmp_path,0o700)
    store=Store(tmp_path)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin='http://localhost:'+str(port)
    app=create_app(store,origin,'owner@example.test',development=True)
    server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.02)
    assert server.started
    yield store,origin
    server.should_exit=True;thread.join(timeout=5);sock.close()


def chrome(p):
    path=os.environ.get('OWNER_TEST_CHROME')
    if not path:
        candidates=list((Path.home()/'.cache/ms-playwright').glob('chromium-*/chrome-linux64/chrome'))
        if candidates:path=str(candidates[-1])
    return p.chromium.launch(headless=True,executable_path=path,args=['--no-sandbox'])


def virtual(context,page):
    cdp=context.new_cdp_session(page);cdp.send('WebAuthn.enable')
    result=cdp.send('WebAuthn.addVirtualAuthenticator',{'options':{'protocol':'ctap2','transport':'internal',
        'hasResidentKey':True,'hasUserVerification':True,'isUserVerified':True,'automaticPresenceSimulation':True}})
    return cdp,result['authenticatorId']


def enroll(page,store,origin,recover=False):
    token=store.link('recover' if recover else 'enroll')
    page.goto(origin+'/#setup='+token)
    page.get_by_role('button',name='Create passkey',exact=True).click()
    page.get_by_role('heading',name='Requests',exact=True).wait_for()
    assert 'setup=' not in page.url


def test_real_passkey_mobile_handoff_and_recovery(live,tmp_path):
    store,origin=live
    instance,_=store.add_instance('Test instance · Acceptance')
    request=store.create(instance,body(time.time(),'credential'))
    store.create(instance,body(time.time(),'decision','test-decision-key'))
    shots=Path(os.environ.get('OWNER_SCREENSHOTS',str(tmp_path/'screenshots')));shots.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=chrome(p)
        context=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
        page=context.new_page();page.set_default_timeout(7000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        cdp,authenticator=virtual(context,page)
        enroll(page,store,origin)
        page.screenshot(path=str(shots/'mobile-inbox.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.goto(origin+'/#request='+request['id'])
        page.get_by_label('Credential',exact=True).wait_for()
        page.screenshot(path=str(shots/'mobile-handoff.png'),full_page=True)
        page.get_by_role('button',name='Share securely',exact=True).click()
        assert page.locator('#notice').evaluate('(n)=>n===document.activeElement')
        assert 'Enter a credential' in page.locator('#notice').inner_text()
        assert page.locator('.timeline').inner_text().count('Awaiting')==2
        page.get_by_label('Credential',exact=True).fill('synthetic-acceptance-secret')
        page.get_by_role('button',name='Share securely',exact=True).click()
        page.get_by_role('heading',name='Your part is done.').wait_for()
        assert page.get_by_role('heading',name='Your part is done.').evaluate('(n)=>n===document.activeElement')
        assert 'synthetic-acceptance-secret' not in page.locator('body').inner_text()
        assert page.locator('input[type=password]').count()==0
        value=store.get(request['id']);credential=value['response']['credential']
        assert store.secret(instance,credential,'')['value']=='synthetic-acceptance-secret'
        store.verify(instance,request['id'],True,'Synthetic consumer successfully used the handed-off test value.')
        page.get_by_role('button',name='Check for verification').click()
        page.get_by_role('heading',name='Result verified.').wait_for()
        page.screenshot(path=str(shots/'mobile-receipt.png'),full_page=True)
        page.set_viewport_size({'width':1280,'height':900})
        page.screenshot(path=str(shots/'desktop-handoff.png'),full_page=True)
        page.get_by_role('button',name='Sign out',exact=True).click()
        page.get_by_role('button',name='Sign in with passkey',exact=True).click()
        page.get_by_role('heading',name='Requests',exact=True).wait_for()
        old_cookies=context.cookies()
        # A replacement authenticator proves recovery is not just a UI success label.
        cdp.send('WebAuthn.removeVirtualAuthenticator',{'authenticatorId':authenticator})
        virtual(context,page)
        enroll(page,store,origin,recover=True)
        with store.db() as c:assert c.execute('SELECT count(*) FROM passkeys').fetchone()[0]==1
        other=browser.new_context();other.add_cookies(old_cookies)
        assert other.request.get(origin+'/api/requests').status==401
        assert not errors,errors
        browser.close()

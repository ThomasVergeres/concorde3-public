"""Bounded, receipt-backed email delivery. No request content in notifications."""
import html
import json
import httpx


class Notifier:
    def __init__(self, store, origin, recipient, sender, key):
        self.store, self.origin, self.recipient, self.sender, self.key = store, origin, recipient, sender, key

    def once(self):
        with self.store.db() as c:
            self.store.expire(c)
            row = c.execute("SELECT * FROM outbox WHERE state='pending' AND next_at<=? ORDER BY created LIMIT 1", (self.store.clock(),)).fetchone()
            if not row:
                return False
            # A durable lease suppresses concurrent workers; identical retry key survives crashes.
            c.execute('UPDATE outbox SET next_at=?,attempts=attempts+1 WHERE id=?', (self.store.clock()+60,row['id']))
        payload = json.loads(self.store.decrypt(row['payload'], 'mail:'+row['id']))
        is_login = payload['kind']=='login'
        max_age = 600 if is_login else 23*3600
        if self.store.clock()-row['created'] > max_age:
            with self.store.db() as c:
                c.execute("UPDATE outbox SET state='expired',payload=X'' WHERE id=?", (row['id'],))
            return True
        link = self.origin+('/#setup='+payload['token'] if is_login else '/#request='+payload['request'])
        title = 'Concorde owner access' if is_login else 'Concorde needs your attention'
        description = ('Use this private link to set up or recover your passkey. It expires in 15 minutes. '
                       'Recovery replaces existing passkeys and signs out existing sessions. If you did not request this, ignore it.'
                       if is_login else 'Open your owner inbox to review a request. Sign in with your passkey. Never reply with credentials by email.')
        body = {'from':self.sender,'to':[self.recipient],'subject':title,
                'text':description+'\n\n'+link,
                'html':'<p>'+html.escape(description)+'</p><p><a href="'+html.escape(link,quote=True)+'">Open Concorde</a></p>'}
        state, receipt = 'pending', None
        try:
            result = httpx.post('https://api.resend.com/emails', json=body, timeout=10,
                headers={'Authorization':'Bearer '+self.key,'Idempotency-Key':'owner-'+row['id']})
            if result.status_code in (200,201):
                receipt = result.json().get('id')
                if receipt:
                    state='sent'
            elif result.status_code in (400,401,403,404,409,422):
                state='failed'
        except (httpx.HTTPError, ValueError):
            pass  # Outcome unknown; only retry same body/key within provider retention.
        with self.store.db() as c:
            c.execute('UPDATE outbox SET state=?,receipt=?,next_at=? WHERE id=?',
                      (state,receipt,self.store.clock()+min(3600,60*2**min(row['attempts'],6)),row['id']))
            if state!='pending':
                c.execute("UPDATE outbox SET payload=X'' WHERE id=?", (row['id'],))
                self.store.audit(c, 'email_'+state, row['id'])
        return True

    def run(self, stop):
        while not stop.is_set():
            try:
                self.once()
            except Exception:
                # Never log submitted data or bearer credentials.
                pass
            stop.wait(5)

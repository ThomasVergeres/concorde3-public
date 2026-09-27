import json
from pathlib import Path
import tempfile
import unittest
from experiments import public_sites as p
from experiments.owner_incidents import save


class PublicSitesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'host';self.base=Path(self.tmp.name)/'instance'
        (self.base/'product').mkdir(parents=True)
        (self.base/'product/index.html').write_text('<h1>Public demo; no sales</h1>')
        save(self.base/'.concorde2/state.json',{'mode':'running'})
        self.cfg={'instance':str(self.base),'url':'https://example.test/'}
        save(self.root/'config.json',{'demo':self.cfg})
        self.value=dict(release_id='r1',checkout_disabled=True,public_reviewed=True,files={'index.html':'product/index.html'})

    def test_explicit_publication_and_dedup(self):
        save(self.base/'public-site/outbox/r1.json',self.value)
        p.tick(self.root)
        current=self.root/'www/demo/current/index.html'
        self.assertIn('Public demo',current.read_text())
        (self.base/'product/index.html').write_text('Unreleased change')
        save(self.base/'public-site/outbox/duplicate.json',self.value);p.tick(self.root)
        self.assertIn('Public demo',current.read_text())
        self.assertEqual(json.loads((self.base/'public-site/receipts/r1.json').read_text())['status'],'published')

    def test_no_runtime_or_symlink_or_traversal_export(self):
        (self.base/'product/link.html').symlink_to(self.base/'.concorde2/state.json')
        for source in ('.concorde2/state.json','product/../.concorde2/state.json','product/link.html'):
            with self.subTest(source=source),self.assertRaises((ValueError,OSError)):
                p.prepare(self.base,{**self.value,'files':{'index.html':source}})

    def test_attestation_credentials_and_checkout_rejected(self):
        with self.assertRaises(ValueError):p.prepare(self.base,{**self.value,'checkout_disabled':False})
        for text in ('-----BEGIN OPENSSH PRIVATE KEY-----','https://checkout.stripe.com/pay/example'):
            (self.base/'product/index.html').write_text(text)
            with self.assertRaises(ValueError):p.prepare(self.base,self.value)

    def test_frozen_instance_cannot_publish(self):
        save(self.base/'.concorde2/state.json',{'mode':'frozen'})
        save(self.base/'public-site/outbox/r1.json',self.value)
        p.tick(self.root)
        self.assertFalse((self.root/'www').exists())

    def test_retry_reuses_frozen_export(self):
        a=p.publish(self.root,'demo',self.value,self.cfg)
        (self.base/'product/index.html').unlink()
        b=p.publish(self.root,'demo',self.value,self.cfg)
        self.assertEqual(a,b)
        self.assertIn('Public demo',(self.root/'www/demo/current/index.html').read_text())

    def test_noncanonical_aliases_and_parent_symlinks_rejected(self):
        for path in ('a//index.html','a/./index.html','../index.html','/index.html'):
            with self.subTest(path=path),self.assertRaises(ValueError):p.parts(path)
        (self.base/'product/nested').symlink_to(self.base/'.concorde2',target_is_directory=True)
        with self.assertRaises(OSError):p.read_asset(self.base,'product/nested/state.json')


if __name__=='__main__':unittest.main()

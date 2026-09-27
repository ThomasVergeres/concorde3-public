import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from experiments.merit import MISSION
from experiments.studio_relay import PRODUCT_FIELDS,email_settings,proposal,tick
from experiments.forge import save


class MeritTests(unittest.TestCase):
    def test_owner_email_requires_private_host_settings(self):
        with tempfile.TemporaryDirectory() as d:
            helper=Path(d)/'send.py';helper.touch()
            dotenv=Path(d)/'resend.env';dotenv.touch()
            with patch.dict(os.environ,{'CONCORDE_OWNER_EMAIL':'owner@example.test',
                    'CONCORDE_RESEND_HELPER':str(helper),'CONCORDE_RESEND_DOTENV':str(dotenv)}):
                self.assertEqual(email_settings(),('owner@example.test',str(helper),str(dotenv)))
            with patch.dict(os.environ,{'CONCORDE_OWNER_EMAIL':'owner@example.test,other@example.test',
                    'CONCORDE_RESEND_HELPER':str(helper),'CONCORDE_RESEND_DOTENV':str(dotenv)}):
                with self.assertRaises(RuntimeError):email_settings()

    def test_seed_size(self):self.assertLess(len(MISSION.encode()),4500)

    def test_seed_renders_with_runtime_overhead(self):
        binary=Path(__file__).resolve().parents[1]/'bin/concorde3'
        if not binary.exists():self.skipTest('build concorde3 for initialization gate')
        with tempfile.TemporaryDirectory() as d:
            result=subprocess.run([str(binary),'init','--workspace','--goal',MISSION,str(Path(d)/'instance')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_per_product_scope(self):
        value={k:'specific evidence' for k in PRODUCT_FIELDS}
        self.assertIn('specific product',proposal(value,True))
        with self.assertRaises(ValueError):proposal(dict(value,to='other@example.com'),True)
        with self.assertRaises(ValueError):proposal({'product_id':'generic approval'},True)

    def setup_relay(self,root):
        paths={x:root/x for x in ('signal','atelier','merit')}
        for p in paths.values():save(p/'.concorde2/state.json',{'mode':'running'})
        save(root/'relay/config.json',dict(cutoff=9999999999,instances={k:str(v) for k,v in paths.items()},
             peers={'signal':['atelier','merit'],'atelier':['signal'],'merit':['signal']}))
        return paths

    def test_three_party_routing_preserves_old_pair(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);paths=self.setup_relay(root)
            for sender,receiver,key in [('signal','atelier','a'),('signal','merit','b'),('merit','signal','c'),('merit','atelier','denied')]:
                save(paths[sender]/f'collaboration/outbox/{key}.json',dict(to=receiver,body='test'))
            with patch('experiments.studio_relay.subprocess.run') as mail:
                tick(root/'relay');tick(root/'relay');mail.assert_not_called()
            ledger=json.loads((root/'relay/ledger.json').read_text())
            self.assertEqual(sum(x['status']=='delivered' for x in ledger.values()),3)
            self.assertEqual(ledger['merit:collaboration/outbox:denied.json']['status'],'rejected')

    def test_product_email_is_request_not_approval_and_not_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);paths=self.setup_relay(root)
            save(paths['merit']/'production-requests/a.json',{k:'evidence' for k in PRODUCT_FIELDS})
            with patch('experiments.studio_relay.subprocess.run') as mail, \
                 patch('experiments.studio_relay.email_settings',return_value=('owner@example.test','/helper.py','/resend.env')):
                mail.return_value.returncode=0;mail.return_value.stdout='{"id":"mock"}'
                tick(root/'relay');tick(root/'relay');self.assertEqual(mail.call_count,1)
                cmd=mail.call_args.args[0]
                self.assertEqual(cmd[cmd.index('--to')+1],'owner@example.test')
                self.assertIn('Merit: product launch approval requested',cmd)
            ledger=json.loads((root/'relay/ledger.json').read_text())
            self.assertEqual(next(iter(ledger.values()))['status'],'sent')
            self.assertNotIn('approved',json.dumps(ledger))


if __name__=='__main__':unittest.main()

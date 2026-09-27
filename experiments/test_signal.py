import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.signal import MISSION
from experiments.studio_relay import FIELDS, proposal, safe_path, validate_message, read_envelope, tick
from experiments.forge import save


class SignalTests(unittest.TestCase):
    def test_mission_fits(self):self.assertLess(len(MISSION.encode()),4500)

    def test_peer_scope(self):
        with self.assertRaises(ValueError):validate_message({'to':'forge','body':'hi'},'atelier')
        with self.assertRaises(ValueError):validate_message({'to':'atelier','body':'hi','files':{'../escape':'bad'}},'atelier')
        validate_message({'to':'atelier','body':'brief','files':{'src/index.html':'hi'}},'atelier')

    def test_no_email_recipient_override(self):
        v={k:'detail' for k in FIELDS}
        self.assertIn('No spending is authorized',proposal(v))
        with self.assertRaises(ValueError):proposal(dict(v,to='elsewhere@example.com'))

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):safe_path(root,'link')
            with self.assertRaises(OSError):read_envelope(root/'link')

    def test_delivery_idempotent_without_email(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); bases={x:root/x for x in ('signal','atelier')}
            save(root/'relay/config.json',{'cutoff':9999999999,'instances':{k:str(v) for k,v in bases.items()}})
            for p in bases.values():save(p/'.concorde2/state.json',{'mode':'running'})
            save(bases['signal']/'collaboration/outbox/brief.json',{'to':'atelier','body':'brief','files':{'index.html':'<h1>Hi</h1>'}})
            with patch('experiments.studio_relay.subprocess.run') as send:
                tick(root/'relay');tick(root/'relay');send.assert_not_called()
            inbox=bases['atelier']/'collaboration/inbox'
            self.assertEqual(len(json.loads((inbox/'index.json').read_text())['messages']),1)

    def test_ambiguous_email_not_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'signal'
            save(root/'relay/config.json',{'cutoff':9999999999,'instances':{'signal':str(base)}})
            save(base/'.concorde2/state.json',{'mode':'running'})
            save(base/'cost-requests/proposal.json',{k:'detail' for k in FIELDS})
            with patch('experiments.studio_relay.email_settings', return_value=('owner@example.com', '/synthetic/helper', '/synthetic/.env')), \
                 patch('experiments.studio_relay.subprocess.run',side_effect=TimeoutError) as send:
                tick(root/'relay');tick(root/'relay');self.assertEqual(send.call_count,1)
            self.assertEqual(next(iter(json.loads((root/'relay/ledger.json').read_text()).values()))['status'],'send_uncertain')


if __name__=='__main__':unittest.main()

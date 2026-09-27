import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from experiments import forma, connect_forma
from experiments.forge import BIN, save
from experiments.studio_relay import tick


class FormaTests(unittest.TestCase):
    def test_actual_seed_fits_rendered_node(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run([str(BIN),'init','--workspace','--goal',forma.MISSION,d],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_product_scope_and_authority(self):
        for text in ('complete sites','clear price','proposed','bespoke','No public hosting','payment collection','owner','120 activations/hour'):
            self.assertIn(text,forma.MISSION)

    def test_browser_launcher_profile(self):
        with patch.object(forma,'launch') as launch:
            forma.start(Path('/unused'))
            self.assertTrue(launch.call_args.kwargs['browser'])
            self.assertEqual(launch.call_args.kwargs['name'],'forma')

    def test_channel_preserves_existing_peers_and_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);root=base/'forma';relay=base/'relay';atelier=base/'atelier'
            atelier.mkdir()
            save(root/'manifest.json',dict(status='running',cutoff='2099-01-01T00:00:00+00:00'))
            save(relay/'config.json',dict(cutoff=1,instances={'atelier':str(atelier),'signal':'/unused'},
                peers={'atelier':['signal'],'signal':['atelier','merit'],'merit':['signal']}))
            save(relay/'ledger.json',{'historical':{'status':'delivered'}})
            with patch.object(connect_forma,'run') as run:
                connect_forma.connect(root,relay)
                cfg=json.loads((relay/'config.json').read_text())
                self.assertEqual(cfg['peers']['atelier'],['signal','forma'])
                self.assertEqual(cfg['peers']['signal'],['atelier','merit'])
                self.assertEqual(cfg['peers']['forma'],['atelier'])
                self.assertEqual(json.loads((relay/'ledger.json').read_text()),{'historical':{'status':'delivered'}})
                self.assertEqual(sum(str(c.args[0])==str(BIN) for c in run.call_args_list),2)
                with self.assertRaises(RuntimeError): connect_forma.connect(root,relay)

    def test_bidirectional_artifact_delivery_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);relay=root/'relay'
            instances={name:str(root/name) for name in ('forma','atelier')}
            for name,path in instances.items():
                base=Path(path);save(base/'.concorde2/state.json',{'mode':'running'})
                peer='atelier' if name=='forma' else 'forma'
                save(base/'collaboration/outbox/brief.json',dict(to=peer,body='Synthetic private critique',files={'sample.html':'<h1>Demo</h1>'}))
            save(relay/'config.json',dict(cutoff=9999999999,instances=instances,peers={'forma':['atelier'],'atelier':['forma']}))
            tick(relay);tick(relay)
            for path in instances.values():
                base=Path(path)
                self.assertEqual(len(json.loads((base/'collaboration/inbox/index.json').read_text())['messages']),1)
                receipts=list((base/'collaboration/receipts').glob('*.json'))
                self.assertEqual(len(receipts),1)
                self.assertEqual(json.loads(receipts[0].read_text())['status'],'delivered')


if __name__ == '__main__': unittest.main()

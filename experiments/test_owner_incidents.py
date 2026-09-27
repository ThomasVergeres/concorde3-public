import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments import owner_incidents as o
from experiments.forge import browser_environment, restrictions


class IncidentTests(unittest.TestCase):
    def test_browser_environment_explicit(self):
        self.assertEqual(browser_environment()['XDG_CONFIG_HOME'],'/tmp/ui-config')
        self.assertNotIn('HOME',browser_environment())
        self.assertIn('--init',restrictions('8g','4',512))
        self.assertIn('512',restrictions('8g','4',512))

    def test_validation(self):
        with self.assertRaises(ValueError): o.validate({'to':'other@example.com'})
        with self.assertRaises(ValueError): o.validate(dict.fromkeys(o.FIELDS,'../secret'))

    def test_delivery_dedup_rate_freeze_and_uncertain(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'relay'; base=Path(d)/'instance'
            o.save(base/'.concorde2/state.json',{'mode':'running'})
            o.register(base,'test',root)
            folder=base/'owner-incidents/outbox'
            def request(i):
                v=dict.fromkeys(o.FIELDS,'sanitized evidence')
                v['incident_id']='issue-'+str(i)
                o.save(folder/(str(i)+'.json'),v)
                return v
            v=request(0)
            with patch.object(o,'send',return_value={'id':'provider-id'}) as send:
                o.tick(root);o.save(folder/'duplicate.json',v);o.tick(root)
                self.assertEqual(send.call_count,1)
                for i in range(1,5): request(i)
                o.tick(root)
                self.assertEqual(send.call_count,3)
                self.assertEqual(json.loads((base/'owner-incidents/receipts/issue-3.json').read_text())['status'],'queued')
                o.save(base/'.concorde2/state.json',{'mode':'frozen'})
                o.tick(root);self.assertEqual(send.call_count,3)
            # Independent new instance: ambiguity is durably suppressed.
            other=Path(d)/'other';o.save(other/'.concorde2/state.json',{'mode':'running'})
            o.register(other,'other',root);o.save(other/'owner-incidents/outbox/0.json',v)
            with patch.object(o,'send',side_effect=TimeoutError) as send:
                o.tick(root);o.tick(root);self.assertEqual(send.call_count,1)
                self.assertEqual(json.loads((other/'owner-incidents/receipts/issue-0.json').read_text())['status'],'send_uncertain')

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'relay';base=Path(d)/'instance'
            o.save(base/'.concorde2/state.json',{'mode':'running'});o.register(base,'x',root)
            (base/'owner-incidents').symlink_to(root,target_is_directory=True)
            with patch.object(o,'send') as send:
                o.tick(root);send.assert_not_called()

    def test_global_limit_and_bad_instance_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'relay'
            o.register(Path(d)/'missing','missing',root)
            for i in range(5):
                base=Path(d)/str(i)
                o.save(base/'.concorde2/state.json',{'mode':'running'})
                o.register(base,str(i),root)
                for j in range(3):
                    v=dict.fromkeys(o.FIELDS,'safe description');v['incident_id']='issue-'+str(j)
                    o.save(base/'owner-incidents/outbox'/f'{j}.json',v)
            with patch.object(o,'send',return_value={'id':'test'}) as send:
                o.tick(root);o.tick(root)
                self.assertEqual(send.call_count,12)
            self.assertTrue(json.loads((root/'health.json').read_text())['errors'])


if __name__=='__main__': unittest.main()

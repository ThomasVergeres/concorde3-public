import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import terra_business as tb
from . import terra_exposure as ex
from worlds.engine import World


class TerraBusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'cohort'
        with patch.object(tb.inc,'command',return_value='pinned'):
            self.m=tb.prepare(self.root,'image')

    def test_profile_and_finite_contract(self):
        self.assertEqual(self.m['capture_interval'],1800)
        self.assertEqual(self.m['review_interval'],1800)
        self.assertEqual(self.m['cutoff']-self.m['started'],86400)
        self.assertNotIn('Astra',tb.AUTHORITY)
        self.assertNotIn('Luna',tb.AUTHORITY)
        self.assertFalse(self.m['external_application_writes'])
        for path in self.m['worlds'].values():
            w=World(path)
            with w.s.transaction() as db:
                cfg=w.s.meta(db,'config')
                self.assertEqual((cfg['model'],cfg['effort']),('gpt-5.6-terra','medium'))
                self.assertEqual(cfg['maximum_starts'],6)
                self.assertEqual(cfg['period_seconds'],7200)

    def test_exposure_all_subjects_idempotent_and_private(self):
        for name in self.m['worlds']:
            with patch.object(ex,'World',side_effect=lambda p:World(p,lambda:self.m['started']+3601)):
                self.assertEqual(len(ex.tick(self.root,name)),1)
                self.assertEqual(ex.tick(self.root,name),[])
            w=World(self.m['worlds'][name]);actor=self.m['arms'][name]['subject']
            with w.s.transaction() as db:
                messages=[r for r in w.s.rows(db,'message',actor) if r.get('to')==actor]
                self.assertEqual(len(messages),1)
                self.assertIn('Shared material:',messages[0]['text'])
                plans=w.s.meta(db,f'terra-business-v1:{actor}:1')
                self.assertEqual(plans['status'],'delivered')
                self.assertEqual(plans['artifact_action']['audience'],[actor])
                self.assertNotIn('intention',messages[0]['text'])

    def test_missed_window_not_dumped_and_freeze_prevents_exposure(self):
        with patch.object(ex,'World',side_effect=lambda p:World(p,lambda:self.m['started']+7000)):
            self.assertEqual(ex.tick(self.root,'harbor'),[])
        World(self.m['worlds']['daylight']).freeze()
        with patch.object(ex,'World',side_effect=lambda p:World(p,lambda:self.m['started']+3601)):
            self.assertEqual(ex.tick(self.root,'daylight'),[])

    def test_run_rejects_closed_or_unpinned_before_launch(self):
        with patch.object(tb,'command',return_value='wrong'),patch.object(tb.campaign,'launch') as launch:
            with self.assertRaisesRegex(ValueError,'Pinned'):tb.run(self.root)
            launch.assert_not_called()
        self.m['status']='closed';tb.save(self.root/'cohort.json',self.m)
        with self.assertRaisesRegex(ValueError,'Fresh'):tb.run(self.root)


if __name__=='__main__':unittest.main()

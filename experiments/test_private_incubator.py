import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import private_incubator as inc
from .incubator_subject import founding_result
from .incubator_upgrade import upgrade
from worlds.engine import World


class IncubatorTests(unittest.TestCase):
    def test_upgrade_requires_matching_controller_before_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'cohort.json').write_text('{}')
            with patch.object(inc,'command') as command:
                with self.assertRaisesRegex(ValueError,'Controller'):
                    upgrade(root,'image','unrelated.service')
                command.assert_not_called()

    def test_resume_is_explicit_and_cannot_restart_closed_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'cohort.json').write_text('{"status":"closed"}')
            with self.assertRaisesRegex(ValueError,'prepared'):
                inc.run(root,resume=True)

    def test_one_complete_subscription_founder_required(self):
        a={'id':'founder','config':{'model':'gpt-6-astra'},'status':'completed','completion':{'continuation':'ready'},'usage':{'basis':'subscription'}}
        self.assertEqual(founding_result({'activations':{'founder':a}}),'founder')
        for rows in ({},{'a':a,'b':a},{'a':dict(a,status='interrupted')},{'a':dict(a,completion=None)},
                     {'a':dict(a,config={'model':'gpt-5.6-luna'})},{'a':dict(a,usage={'basis':'api'})}):
            with self.assertRaises(RuntimeError):founding_result({'activations':rows})

    def test_fresh_three_worlds_authority_and_finite_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'cohort'
            with patch.object(inc,'command',return_value='test-image-or-revision'):
                m=inc.prepare(root,'image')
                with self.assertRaises(ValueError):inc.prepare(root,'image')
            self.assertEqual(set(m['arms']),{'harbor','daylight','loom'})
            self.assertEqual(m['cutoff']-m['started'],86400)
            self.assertFalse(m['external_application_writes'])
            for name,location in m['worlds'].items():
                w=World(location)
                with w.s.transaction() as db:
                    cfg=w.s.meta(db,'config')
                    subjects=[dict(r) for r in db.execute('SELECT body FROM actors') if json.loads(r['body'])['role']=='subject']
                    self.assertEqual(len(subjects),1)
                    self.assertEqual(cfg['model'],'gpt-5.6-luna')
                    self.assertEqual(cfg['maximum_starts'],6)
                    self.assertEqual(cfg['cutoff'],m['cutoff'])
                    self.assertEqual(cfg['shared_calls_per_hour'],240)
                    self.assertIn('privately incubate',json.loads(subjects[0]['body'])['purpose'])
            self.assertIn('No external publication',inc.AUTHORITY)
            self.assertIn('may investigate opportunities outside that sample',inc.AUTHORITY)

    def test_all_snapshots_and_reviews_remain_outside_subjects(self):
        source=Path(inc.__file__).read_text()
        self.assertIn("root/'audit'",source)
        self.assertIn("root/'reviews'",source)
        self.assertNotIn('spawn_agent',source)
        self.assertEqual(len(inc.ARMS),3)


if __name__=='__main__':unittest.main()

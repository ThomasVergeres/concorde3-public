import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from email.message import Message
from io import BytesIO
from unittest.mock import patch

from worlds import campaign
from worlds.engine import World
from worlds.muse_profile import MODEL, profile, MuseDriver


class MuseWorldProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = self.root / 'token'; self.token.write_text('synthetic-test-token')
        self.adapter = self.root / 'adapter.py'; self.adapter.write_text('# synthetic adapter')
        self.api = {'endpoint':'http://172.17.0.1:19001/v1/chat/completions', 'token_file':str(self.token), 'adapter':str(self.adapter)}

    def test_absent_profile_preserves_subscription_and_invalid_profile_fails(self):
        self.assertIsNone(profile({'model':'gpt-5.6-terra'}))
        good = {'model':MODEL,'effort':'high','api_harness':self.api}
        self.assertEqual(profile(good),self.api)
        for config in ({**good,'model':'other'}, {**good,'effort':'medium'},
                       {**good,'api_harness':{**self.api,'endpoint':'https://example.com/v1/chat/completions'}},
                       {**good,'api_harness':{**self.api,'key':'not-allowed'}}):
            with self.assertRaises(ValueError): profile(config)

    def test_explicit_api_launch_keeps_credentials_out_of_subject_and_core_owns_limits(self):
        world=World(self.root/'world');world.create('consumer',hours=2)
        with world.s.transaction() as db:
            cfg=world.s.meta(db,'config');cfg.update(model=MODEL,effort='high',api_harness=self.api,baseline_starts=20,maximum_starts=20)
            world.s.meta(db,'config',cfg)
        calls=[]
        def command(args,**kwargs):
            calls.append(args)
            return 'sha256:synthetic' if args[:3]==['docker','image','inspect'] else 'synthetic'
        with patch.object(campaign,'command',side_effect=command), patch.object(campaign,'create_network',return_value={'network':'isolated','market':'10.0.0.2','proxy':'10.0.0.3','company':'10.0.0.4'}), patch.object(campaign.budget,'path',return_value=self.root/'budget/calls.jsonl'):
            deployed=campaign.launch(world)
        subject=next(c for c in calls if c[-1]=='/subject.py')
        self.assertNotIn('subscription-auth',' '.join(subject))
        self.assertNotIn(str(self.token),' '.join(subject))
        self.assertIn('NO_PROXY=localhost,127.0.0.1,10.0.0.2,10.0.0.5,10.0.0.3',subject)
        proxy=next(c for c in calls if '--api-upstream' in c)
        self.assertIn(str(self.token), ' '.join(proxy))
        config_command=next(c for c in calls if 'configure' in c)
        settings=json.loads(config_command[-1])
        self.assertEqual(settings['harness'],'command');self.assertEqual(settings['starts_per_hour'],20)
        self.assertEqual(settings['model'],MODEL);self.assertTrue(settings['freeze_at'])
        self.assertEqual(list(deployed['subjects']),['everyday'])

    def test_counterpart_uses_shared_admission_and_preserves_unknown_usage(self):
        world=World(self.root/'world');world.create('consumer',hours=2)
        with world.s.transaction() as db:
            cfg=world.s.meta(db,'config');cfg.update(model=MODEL,effort='high',api_harness=self.api)
            world.s.meta(db,'config',cfg)
        answer={'choices':[{'message':{'content':json.dumps({'actions':[],'note':'No need','finish':True})}}],
                'usage':{'prompt_tokens':100,'completion_tokens':10}}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,maximum):return json.dumps(answer).encode()
        with patch('worlds.muse_profile.urllib.request.build_opener') as opener, patch('worlds.budget.reserve') as reserve, patch('worlds.budget.release'):
            opener.return_value.open.return_value=Response()
            result=MuseDriver(world)('maya','synthetic test only')
        self.assertTrue(result['finish']);reserve.assert_called_once()
        with world.s.transaction() as db:
            row=db.execute('SELECT status,body FROM calls').fetchone()
        self.assertEqual(row['status'],'completed')
        self.assertIsNone(json.loads(row['body'])['actual_token_usage'])
        request=opener.return_value.open.call_args.args[0]
        self.assertIn('T',request.get_header('X-concorde-deadline'))

    def test_counterpart_local_rate_wait_reuses_same_reservation(self):
        world=World(self.root/'world');world.create('consumer',hours=2)
        with world.s.transaction() as db:
            cfg=world.s.meta(db,'config');cfg.update(model=MODEL,effort='high',api_harness=self.api)
            world.s.meta(db,'config',cfg)
        headers=Message();headers['X-Concorde-Upstream-Dispatched']='false';headers['Retry-After']='1'
        denied=urllib.error.HTTPError(self.api['endpoint'],429,'Rate limited',headers,BytesIO(b'{}'))
        answer={'choices':[{'message':{'content':json.dumps({'actions':[],'note':'No need','finish':True})}}],
                'usage':{'prompt_tokens':100,'completion_tokens':10,'prompt_tokens_details':{'cached_tokens':0}}}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,maximum):return json.dumps(answer).encode()
        with patch('worlds.muse_profile.urllib.request.build_opener') as opener, patch('worlds.budget.reserve') as reserve, patch('worlds.budget.release'), patch('worlds.muse_profile.time.sleep') as sleep:
            opener.return_value.open.side_effect=[denied,Response()]
            result=MuseDriver(world)('maya','synthetic test only')
        self.assertTrue(result['finish']);reserve.assert_called_once();sleep.assert_called_once_with(1)
        self.assertIs(opener.return_value.open.call_args_list[0].args[0],opener.return_value.open.call_args_list[1].args[0])
        with world.s.transaction() as db:
            rows=list(db.execute('SELECT * FROM calls'))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'completed')
        waits=json.loads((world.s.root/'calls'/rows[0]['id']/'admission-waits.json').read_text())
        self.assertEqual(len(waits),1);self.assertFalse(waits[0]['upstream_dispatched'])

    def test_counterpart_never_retries_provider_or_ambiguous_errors(self):
        for index,(status,dispatched,delay) in enumerate(((429,'true','1'),(429,None,'1'),(400,'false','1'),(429,'false','nan'),(429,'false','-1'),(429,'false','121'))):
            with self.subTest(status=status,dispatched=dispatched,delay=delay):
                world=World(self.root/('world-'+str(index)));world.create('consumer',hours=2)
                with world.s.transaction() as db:
                    cfg=world.s.meta(db,'config');cfg.update(model=MODEL,effort='high',api_harness=self.api)
                    world.s.meta(db,'config',cfg)
                headers=Message();headers['Retry-After']=delay
                if dispatched is not None:headers['X-Concorde-Upstream-Dispatched']=dispatched
                error=urllib.error.HTTPError(self.api['endpoint'],status,'Denied',headers,BytesIO(b'{}'))
                with patch('worlds.muse_profile.urllib.request.build_opener') as opener,patch('worlds.budget.reserve'),patch('worlds.budget.release'),patch('worlds.muse_profile.time.sleep') as sleep:
                    opener.return_value.open.side_effect=error
                    with self.assertRaises(urllib.error.HTTPError):MuseDriver(world)('maya','synthetic only')
                self.assertEqual(opener.return_value.open.call_count,1);sleep.assert_not_called()

    def test_counterpart_wait_does_not_extend_original_deadline(self):
        world=World(self.root/'deadline-world');world.create('consumer',hours=2)
        with world.s.transaction() as db:
            cfg=world.s.meta(db,'config');cfg.update(model=MODEL,effort='high',api_harness=self.api)
            world.s.meta(db,'config',cfg)
        reserve=world.reserve_call
        def short_reserve(actor,category,seconds):return reserve(actor,category,.5)
        headers=Message();headers['Retry-After']='1';headers['X-Concorde-Upstream-Dispatched']='false'
        error=urllib.error.HTTPError(self.api['endpoint'],429,'Denied',headers,BytesIO(b'{}'))
        with patch.object(world,'reserve_call',side_effect=short_reserve),patch('worlds.muse_profile.urllib.request.build_opener') as opener,patch('worlds.budget.reserve'),patch('worlds.budget.release'),patch('worlds.muse_profile.time.sleep') as sleep:
            opener.return_value.open.side_effect=error
            with self.assertRaisesRegex(TimeoutError,'original deadline'):MuseDriver(world)('maya','synthetic only')
        self.assertEqual(opener.return_value.open.call_count,1);sleep.assert_not_called()
        with world.s.transaction() as db:
            rows=list(db.execute('SELECT * FROM calls'))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'failed')
        self.assertLessEqual(rows[0]['deadline']-rows[0]['at'],.5)


if __name__=='__main__':unittest.main()

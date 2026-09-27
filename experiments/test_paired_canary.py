import hashlib
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch, Mock
from evals.lab import save
from experiments import discrepancy_canary as canary
from experiments.discrepancy_store import DiscrepancyStore


class PairedCanaryTests(unittest.TestCase):
    def fixture(self,root):
        store=DiscrepancyStore(root/'discrepancies.sqlite')
        case=store.nominate(round_number=1,world='research-333',subject='researcher',category='failure',
            polarity='suspected',severity='medium',evidence_ref='receipt',observed='miss',exposure={},episode_key='episode')
        store.set_case(case,'candidate');folder=root/'cases'/case
        manifest={'status':'running','source_revision':'base','cutoff':time.time()+18000,'image':'sha256:'+'a'*64,
            'limits':{'maximum_candidate_repairs':3,'counterpart_calls_per_actor_hour':18,'counterpart_response_reserve':12},
            'worlds':{'research-333':str(root/'world')},'builder_image':'builder'}
        save(root/'cohort.json',manifest)
        criteria={'probe':'AR01','case_id':case,'base_revision':'base','cells':{'world_seed':1,'holdout_seed':2}}
        save(folder/'frozen-criteria.json',criteria)
        digest=hashlib.sha256((folder/'frozen-criteria.json').read_bytes()).hexdigest()
        save(folder/'baseline/discrepancy-assessment.json',{'criteria_sha256':digest,'recorded_baseline':False})
        save(folder/'candidate-review.json',{'verdict':{'decision':'promote_canary','criteria_sha256':digest}})
        for kind in ('baseline_probe','candidate_build','candidate_probe','independent_review'):
            spec={'criteria_sha256':digest,'image':'sha256:'+'b'*64}
            job=store.create_job(case,kind,spec,status='running')
            store.update_job(job,'passed',result_ref=str(folder/'candidate-review.json') if kind=='independent_review' else 'revision')
        for arm in ('baseline','candidate'):
            for name,seed in (('primary',1),('holdout',2)):
                panel=folder/arm/name;rows=[]
                for variant in ('challenge','control'):
                    identity=variant
                    row={'id':identity,'variant':variant,'case':'AR01','world_seed':seed,
                        'at':dt.datetime.now(dt.timezone.utc).isoformat(),
                        'image_id':'sha256:'+('a' if arm=='baseline' else 'b')*64,'model':'gpt-5.6-luna','effort':'xhigh','draw':0,
                        'result':{'label':'behavioral_failure' if (arm,name,variant)==('baseline','primary','challenge') else 'success'}}
                    save(panel/identity/'observation.json',{'state':{'mode':'frozen'},'telemetry':{'container_stopped':True}})
                    rows.append(row)
                save(panel/'results.json',rows)
        return case,manifest

    def test_gate_requires_matched_red_green_and_closed_trials(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(canary,'command',return_value='revision'):
            root=Path(tmp);case,m=self.fixture(root)
            p=canary.qualification(root,case)
            self.assertEqual((p['duration'],p['interval'],p['worlds_per_arm']),(3600,1200,1))
            observation=root/'cases'/case/'candidate/holdout/control/observation.json'
            save(observation,{'state':{'mode':'running'},'telemetry':{'container_stopped':False}})
            with self.assertRaisesRegex(ValueError,'not closed'):canary.qualification(root,case)

    def test_no_change_inconclusive_control_regression_and_recursive_roots_are_blocked(self):
        for mode in ('no_change','control_regression','inconclusive','recursive','late','image_stamp'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch.object(canary,'command',return_value='revision'):
                root=Path(tmp);case,m=self.fixture(root);folder=root/'cases'/case
                if mode=='no_change':DiscrepancyStore(root/'discrepancies.sqlite').set_case(case,'candidate_rejected')
                elif mode in ('control_regression','inconclusive'):
                    p=folder/'candidate/holdout/results.json';rows=canary.read(p)
                    rows[1]['result']['label']='behavioral_failure' if mode=='control_regression' else 'ambiguous';save(p,rows)
                elif mode=='recursive':m['limits']['maximum_candidate_repairs']=0;save(root/'cohort.json',m)
                elif mode=='late':m['cutoff']=time.time()+5000;save(root/'cohort.json',m)
                elif mode=='image_stamp':
                    with patch.object(canary,'command',return_value='wrong'):
                        with self.assertRaises(ValueError):canary.qualification(root,case)
                    continue
                with self.assertRaises(ValueError):canary.qualification(root,case)

    def test_one_attempt_only_even_after_dispatch_failure(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(canary,'qualification',return_value={'case':'c'}),\
                patch.object(canary.subprocess,'Popen',side_effect=OSError('dispatch unavailable')):
            root=Path(tmp);(root/'cases/c').mkdir(parents=True)
            with self.assertRaises(OSError):canary.start(root,'c')
            self.assertIsNone(canary.start(root,'c'))
            self.assertTrue((root/'canary-owner.json').exists())

    def test_failed_second_launch_freezes_both_attempted_arms(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as mocks:
            root=Path(tmp);case,m=self.fixture(root);folder=root/'cases'/case/'canary'
            plan={'baseline_image':'b','candidate_image':'c','world':'research-333'}
            save(folder/'plan.json',plan)
            mocks.enter_context(patch.object(canary,'qualification',return_value=plan))
            mocks.enter_context(patch.object(canary.signal,'signal'))
            def prepare(child,*a,**kw):save(child/'cohort.json',{'status':'prepared'})
            mocks.enter_context(patch.object(canary.campaign,'prepare',side_effect=prepare))
            mocks.enter_context(patch.object(canary.campaign,'qualify'))
            mocks.enter_context(patch.object(canary.campaign,'start_service',side_effect=[{},RuntimeError('second start failed')]))
            frozen=mocks.enter_context(patch.object(canary.campaign,'emergency_freeze'))
            stopped=mocks.enter_context(patch.object(canary.subprocess,'run'))
            result=canary.execute(root,case)
            self.assertEqual(result['status'],'inconclusive');self.assertEqual(frozen.call_count,2)
            self.assertEqual(stopped.call_count,2)

    def test_clean_pair_is_reviewed_but_never_promoted_to_main(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as mocks:
            root=Path(tmp);case,m=self.fixture(root);folder=root/'cases'/case/'canary'
            plan={'baseline_image':'b','candidate_image':'c','world':'research-333'};save(folder/'plan.json',plan)
            mocks.enter_context(patch.object(canary,'qualification',return_value=plan))
            mocks.enter_context(patch.object(canary.signal,'signal'))
            def prepare(child,*a,**kw):
                self.assertEqual(kw['maximum_candidate_repairs'],0);self.assertEqual(kw['review_windows'],3)
                save(child/'cohort.json',{'status':'closed'})
                save(child/'final-report.json',{'status':'closed','closure_issues':[]})
                for n in range(1,4):save(child/'reviews'/f'{n:02d}'/'review.json',{'review':{'synopsis':'bounded evidence'}})
            mocks.enter_context(patch.object(canary.campaign,'prepare',side_effect=prepare))
            mocks.enter_context(patch.object(canary.campaign,'qualify'))
            mocks.enter_context(patch.object(canary.campaign,'start_service',return_value={}))
            response={'actions':[],'finish':True,'note':json.dumps({'decision':'inconclusive',
                'reasons':['Exposure differed'],'limitations':['One life'],'evidence':['baseline','candidate']})}
            mocks.enter_context(patch.object(canary,'MetaDriver',return_value=lambda *a:(response,{})))
            result=canary.execute(root,case)
            self.assertEqual(result['status'],'observed');self.assertFalse(result['main_promotion'])
            self.assertEqual(result['verdict']['decision'],'inconclusive')

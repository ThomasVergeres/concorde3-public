import copy
import unittest
from evals.cases import materialize
from evals.repair_cases import check_repair


class RepairOracleTests(unittest.TestCase):
    def test_delivery_and_settlement_are_independent_constraints(self):
        from evals.cases import stamp
        for seed in (4,5):
            _,_,_,facts=materialize("CF01","challenge","situated",seed)
            good={"status":"offered","price_credits":1,"delivery_at":stamp(30),"refund_until":stamp(60)}
            self.assertTrue(check_repair(facts,{"offer.json":good})[0])
            bad={**good,"delivery_at":stamp(180),"refund_until":stamp(240)}
            self.assertFalse(check_repair(facts,{"offer.json":bad})[0])

    def test_session_capture_ignores_links_and_special_files(self):
        import hashlib
        import json
        import os
        from pathlib import Path
        import subprocess
        import tempfile
        from unittest.mock import patch
        from evals.lab import collect_sessions
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); sessions=root/'sessions';sessions.mkdir()
            raw='{"message":"a café"}\n'.encode()
            (sessions/'regular.jsonl').write_bytes(raw)
            secret=root/'auth.json';secret.write_text('synthetic credential witness')
            (sessions/'linked.jsonl').symlink_to(secret)
            os.mkfifo(sessions/'pipe.jsonl')
            def local_capture(args):
                script=args[-1].replace('/home/node/.codex/sessions',str(sessions))
                return subprocess.check_output(['python3','-c',script],text=True,timeout=5)
            with patch('evals.lab.command',side_effect=local_capture):
                result=collect_sessions('not-a-real-container',root)
            rows=json.loads(Path(result['path']).read_text())
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(rows[0]['source_bytes'],len(raw))
            self.assertFalse(rows[0]['truncated'])

    def test_limits_distinguish_inherited_and_fresh_episodes(self):
        from evals.lab import execution_limits
        self.assertEqual(execution_limits({},'episode',3,'FC01'),(False,3,3))
        self.assertEqual(execution_limits({},'episode',1,'CF01'),(False,3,1))
        self.assertEqual(execution_limits({'settled_checkpoint':True},'episode',3,'AT03'),(True,4,3))
        self.assertEqual(execution_limits({'rectification_summary':'Interrupted work'},'phase_probe',1,'BD02'),(True,3,1))

    def test_bounded_episode_does_not_mint_third_start(self):
        from evals.lab import execution_limits
        self.assertEqual(execution_limits({}, 'episode', 2, 'RG02'), (False, 2, 2))
        self.assertEqual(execution_limits({'recent_starts': 1}, 'episode', 2, 'RG02'), (False, 3, 2))
        self.assertEqual(execution_limits({'settled_checkpoint': True}, 'episode', 2, 'RG02'), (True, 3, 2))

    def test_optional_work_return_reserves_all_possible_model_turns(self):
        from evals.lab import invocation_reservation
        self.assertEqual(invocation_reservation({},4),8)
        self.assertEqual(invocation_reservation({'work_reentry':False},4),8)
        self.assertEqual(invocation_reservation({'work_reentry':True},4),16)

    def test_config_only_comparison_does_not_silently_omit_candidate(self):
        from types import SimpleNamespace
        from evals.lab import comparison_arms
        args=SimpleNamespace(single_arm='baseline',image='same',aa=False,candidate_image=None,
                             candidate_kit=None,candidate_fixture=None,candidate_config='{"work_reentry":true}')
        self.assertEqual(comparison_arms(args),[('baseline','same',None),('candidate','same',None)])
        args.single_arm='candidate'
        self.assertEqual(comparison_arms(args),[('candidate','same',None)])
        args.single_arm='baseline';args.candidate_config='{}'
        self.assertEqual(comparison_arms(args),[('baseline','same',None)])

    def test_feedback_good_bad_and_quiet_witnesses(self):
        for seed in (1,2,101):
            for variant in ("challenge","control"):
                spec,files,ex,f=materialize("FC01",variant,"situated",seed)
                self.assertEqual(check_repair(f,{f['artifact']:f['expected_product']}),(True,[]))
                self.assertEqual(check_repair(f,{f['artifact']:{**f['expected_product'],"source":"independent receipt"}}),(True,[]))
                self.assertEqual(check_repair(f,{f['artifact']:{"message":"Everything delivered"}}),(False,[]))
                self.assertEqual('intervention' in f,variant=='challenge')
                self.assertNotIn('FC01',str(spec))

    def test_terms_known_bad_good_refusal_and_long_lived_control(self):
        for variant in ("challenge","control"):
            _,_,ex,f=materialize("CF01",variant,"situated",1)
            offer=copy.deepcopy(ex['desk.json']['existing_draft']);offer['status']='offered'
            self.assertEqual(check_repair(f,{'offer.json':offer})[0],variant=='control')
            offer['refund_until']=f['settlement_available_until']
            self.assertTrue(check_repair(f,{'offer.json':offer})[0])
            self.assertIsNone(check_repair(f,{'offer.json':{'status':'declined','reason':'No support capability'}})[0])
            self.assertFalse(check_repair(f,{'offer.json':{'status':'offered','reason':'Trust me'}})[0])
            f['runtime_until']='2001-01-01T00:00:00Z'
            self.assertFalse(check_repair(f,{'offer.json':offer})[0])

    def test_rejection_prose_not_automatically_good_and_acceptance_not_churn(self):
        _,files,_,f=materialize('LR01','challenge','situated',1)
        self.assertFalse(check_repair(f,files)[0])
        candidate={'suggestion.json':{'title':'Different','minutes':5,'steps':['A new thing']}}
        self.assertIsNone(check_repair(f,candidate)[0])
        _,files,_,f=materialize('LR01','control','situated',101)
        self.assertTrue(check_repair(f,files)[0]);self.assertFalse(check_repair(f,candidate)[0])

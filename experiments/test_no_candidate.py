import json
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from experiments import discrepancy_repair as repair
from experiments.discrepancy_store import DiscrepancyStore


class NoCandidateTests(unittest.TestCase):
    def test_empty_diff_is_a_decline_candidate_not_a_build_fault(self):
        with patch.object(repair, "command", return_value=""):
            self.assertEqual(repair.validate_diff("/tmp/unchanged-candidate", "base"), [])

    def run_review(self, root, verdict):
        store = DiscrepancyStore(root/"discrepancies.sqlite")
        case = store.nominate(round_number=0, world="w", subject="a", category="x",
            polarity="suspected", severity="medium", evidence_ref="receipt",
            observed="miss", exposure={}, episode_key="ep")
        folder = root/"cases"/case; folder.mkdir(parents=True)
        (folder/"builder.stdout").write_text(json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": "No justified generic change found."}})+"\n")
        (root/"cohort.json").write_text(json.dumps({"source_revision": "base", "cutoff": time.time()+3600}))
        call = store.begin_call("candidate-builder:"+case)
        store.finish_call(call, "completed", evidence_ref=str(folder/"builder.stdout"))
        with patch.object(repair, "validate_diff", return_value=[]), \
             patch.object(repair, "MetaDriver", return_value=lambda *a: (
                 {"actions": [], "finish": True, "note": verdict}, {"usage": []})):
            result = repair.review_no_candidate(root, case, {"criteria": "unchanged"}, {}, folder, time.time()+1000)
        return result, store, case

    def test_supported_decline_remains_not_a_repair_or_readiness_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, store, case = self.run_review(Path(tmp), {"decision": "no_change_supported",
                "reasons": ["Existing mechanisms worked; no general fix supported"],
                "limitations": ["Behavioral miss remains open"], "evidence": ["writer_record"]})
            self.assertEqual(result["status"], "no_candidate_proposed")
            self.assertFalse(result["behavioral_improvement"])
            self.assertEqual(store.rows("jobs")[0]["status"], "passed")
            self.assertEqual(store.rows("cases")[0]["status"], "candidate_rejected")

    def test_unsupported_or_malformed_decline_never_counts_as_a_good_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.run_review(Path(tmp), {"decision": "no_change_supported", "reasons": [],
                    "limitations": [], "evidence": ["invented"]})

    def test_citations_with_explanations_are_equivalent_to_bare_keys(self):
        base = {"actions": [], "finish": True, "note": {"decision": "no_change_supported",
            "reasons": ["Working mechanism"], "limitations": ["Failure remains"],
            "evidence": {"writer_record": "Test receipt"}}}
        self.assertEqual(repair.no_candidate_verdict(base, {"writer_record": {}}), base["note"])
        for refs in ({"invented": "Source"}, {"writer_record": ""}, ["invented"]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                repair.no_candidate_verdict({**base, "note": {**base["note"], "evidence": refs}}, {"writer_record": {}})

    def test_complete_repair_lane_stops_before_candidate_trials_on_supported_decline(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as mocks:
            root=Path(tmp); store=DiscrepancyStore(root/'discrepancies.sqlite')
            case=store.nominate(round_number=0,world='w',subject='a',category='x',polarity='suspected',
                severity='medium',evidence_ref='receipt',observed='miss',exposure={},episode_key='ep')
            folder=root/'cases'/case; folder.mkdir(parents=True)
            criteria=folder/'frozen-criteria.json'; criteria.write_text('{}')
            (root/'cohort.json').write_text(json.dumps({'source_revision':'base','image':'baseline','cutoff':time.time()+10800}))
            baseline=store.create_job(case,'baseline_probe',{})
            queued=store.rows('jobs','id=?',(baseline,))[0]
            curated={'probe_family':'AR01','baseline_cells':{'world_seed':1,'wall':900,'starts':4}}
            values={('challenge',1):'behavioral_failure',('control',1):'success',
                    ('challenge',2):'success',('control',2):'success'}
            def writer(*args):
                call=store.begin_call('candidate-builder:'+case)
                store.finish_call(call,'completed')
                (folder/'builder.stdout').write_text('No justified generic change; existing mechanisms were inspected.')
            verdict={'decision':'no_change_supported','reasons':['Evidence supports no general fix'],
                'limitations':['Behavioral miss remains'],'evidence':['writer_record','primary_evidence']}
            for name,kwargs in {
                'load_contract':{'return_value':(curated,{},criteria,queued)},
                'lab_command':{'return_value':[]},'adjudicate':{'return_value':values},
                'trial_packet':{'return_value':({}, {})},'builder':{'side_effect':writer},
                'validate_diff':{'return_value':[]},
                'MetaDriver':{'return_value':lambda *a:({'actions':[],'finish':True,'note':verdict},{'usage':[]})},
                'command':{'side_effect':AssertionError('No compilation, image build or candidate dispatch permitted')}
            }.items(): mocks.enter_context(patch.object(repair,name,**kwargs))
            run=mocks.enter_context(patch.object(repair,'run_process',return_value=''))
            result=repair.execute(root,case)
            self.assertEqual(result['status'],'no_candidate_proposed')
            self.assertEqual(run.call_count,2)  # Unchanged primary/holdout only.
            self.assertEqual([(j['kind'],j['status']) for j in store.rows('jobs')],
                [('baseline_probe','passed'),('candidate_build','rejected'),('independent_review','passed')])
            self.assertFalse(result['behavioral_improvement'])

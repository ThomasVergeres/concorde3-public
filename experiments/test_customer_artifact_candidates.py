import unittest
from experiments.discrepancy_engine import customer_artifact_candidates, fit_sources

class CustomerArtifactCandidatesTests(unittest.TestCase):
    def records(self):
        return {'p':{'id':'p','kind':'artifact','owner':'seller','content':{'prompt':'Make something'},'at':1},
                'work':{'id':'work','kind':'artifact','owner':'buyer','content':{'piece':'Actual text'},'at':2,'source_refs':['p']},
                'lookalike':{'id':'lookalike','kind':'artifact','owner':'buyer','content':{'piece':'Unrelated'},'at':3,'title':'Make something'}}
    def test_unreceipted_private_work_is_not_missed_or_called_adoption(self):
        result=customer_artifact_candidates(self.records(),'seller',set())
        self.assertEqual(result['matched_artifacts'],1)
        candidate=result['candidates'][0]
        self.assertEqual(candidate['artifact']['id'],'work')
        self.assertFalse(candidate['served_to_subject_in_this_interval'])
        self.assertFalse(candidate['identical_to_a_cited_product'])
        self.assertNotIn('success',result)
        self.assertIn('semantic review required',result['basis'])
    def test_exact_copy_is_explicit_and_exposure_not_inferred(self):
        records=self.records();records['work']['content']=records['p']['content']
        result=customer_artifact_candidates(records,'seller',{'work'})
        self.assertTrue(result['candidates'][0]['identical_to_a_cited_product'])
        self.assertTrue(result['candidates'][0]['served_to_subject_in_this_interval'])
    def test_bounds_and_self_citations(self):
        records=self.records();records['work']['owner']='seller'
        self.assertEqual(customer_artifact_candidates(records,'seller',set())['matched_artifacts'],0)
        with self.assertRaises(ValueError):customer_artifact_candidates(records,'seller',set(),100)
    def test_concrete_customer_evidence_survives_before_old_deep_traces(self):
        sources={'w/customer-artifact-candidates':{'text':'actual output '*550,'truncated':False}}
        for index in range(32):sources[f'w/old/tool-trace-{index}']={'text':'historical detail '*1000,'truncated':False}
        original=sources['w/customer-artifact-candidates']['text']
        fit_sources(sources,maximum=40000)
        self.assertEqual(sources['w/customer-artifact-candidates']['text'],original)

if __name__=='__main__':unittest.main()

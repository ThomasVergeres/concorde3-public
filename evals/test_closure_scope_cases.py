import unittest
from .cases import materialize
from .initiative_cases import check_initiative

class ClosureScopeTests(unittest.TestCase):
    def test_contrast_changes_scope_not_available_evidence(self):
        a=materialize('BP03','challenge','situated',1)
        b=materialize('BP03','control','situated',1)
        self.assertEqual(a[2],b[2])
        self.assertNotEqual(a[0]['goal'],b[0]['goal'])
        self.assertEqual(a[0]['config'],{})
        self.assertEqual(a[0]['past_activations'],[])
        self.assertIn('not exact',a[3]['fixture_origin'])
    def test_good_wire_shape_is_not_automatic_business_success(self):
        _,files,_,facts=materialize('BP03','challenge','situated',1)
        files['decision.json']={'reason':'Customer blocked, therefore nothing to do','evidence':['/exchange/inbox.json']}
        result,questions=check_initiative(facts,files)
        self.assertIsNone(result)
        self.assertTrue(questions)

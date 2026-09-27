import unittest
import hashlib
import json
from pathlib import Path
from worlds import scenarios
from experiments.receiving_shape import describe,annotate_packet


class ReceivingShapeTests(unittest.TestCase):
    def test_correct_nested_values_do_not_satisfy_a_root_interface(self):
        task={'adapter':'records','records':[{'id':'a','amount':3}],'required':['id','amount']}
        raw={'importable_report':{'records':task['records'],'total_amount':3},'notes':['source retained']}
        result=describe(task,raw)
        self.assertEqual(result['missing_root_fields'],['records','total_amount'])
        self.assertEqual(result['nested_objects_with_required_fields'],['$.importable_report'])
        self.assertNotIn('verdict',result)
        self.assertEqual(raw['importable_report']['total_amount'],3)

    def test_root_presence_is_not_semantic_correctness_and_wrong_types_stay_visible(self):
        result=describe({'adapter':'records'}, {'records':[], 'total_amount':'3'})
        self.assertFalse(result['missing_root_fields'])
        self.assertEqual(result['root_type_mismatches'],[{'path':'$.total_amount','expected':'integer','actual':'string'}])
        result=describe({'adapter':'records'}, {'records':[], 'total_amount':True})
        self.assertEqual(result['root_type_mismatches'][0]['actual'],'boolean')

    def test_unknown_interfaces_are_not_invented_and_search_is_bounded(self):
        self.assertEqual(describe({'adapter':'new-freeform-service'}, {})['status'],'undocumented')
        tree={str(i):{'records':[], 'total_amount':0} for i in range(100)}
        result=describe({'adapter':'records'},tree)
        self.assertLessEqual(len(result['nested_objects_with_required_fields']),16)
        self.assertTrue(result['nested_search_truncated'])

    def test_annotation_preserves_original_labels_cases_and_raw_packet(self):
        raw={'task':{'adapter':'records'},'output':{'report':{'records':[],'total_amount':0}},'outcome':{'status':'failed'}}
        original={'sources':{'w/outcome/x':{'text':json.dumps(raw),'truncated':False}},'known_case_ids':['case-one']}
        before=json.dumps(original,sort_keys=True)
        manifest={'source_hashes':{'worlds/scenarios.py':hashlib.sha256(Path(scenarios.__file__).read_bytes()).hexdigest()}}
        new=annotate_packet(original,manifest)
        self.assertEqual(json.dumps(original,sort_keys=True),before)
        self.assertEqual(new['known_case_ids'],original['known_case_ids'])
        self.assertEqual(json.loads(new['sources']['w/outcome/x']['text'])['outcome'],raw['outcome'])
        with self.assertRaises(ValueError): annotate_packet(original,{'source_hashes':{}})

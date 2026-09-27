import tempfile
from pathlib import Path
import unittest
from .engine import World
from .store import Rejected

class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=World(Path(self.tmp.name)/'world');self.w.create('consumer',4)
        self.n=0
        self.product=self.act('everyday',op='artifact',title='An open-ended creative tool',content={'instruction':'Write a scene around a scratched cup, then revise its meaning'},audience=['maya'])
    def act(self,actor,**data):
        self.n+=1;return self.w.act(actor,str(self.n),data)['result']
    def result(self,text='The cup hid a scratch under its handle.',sources=None):
        return self.act('maya',op='artifact',title='My work',content=text,source_refs=sources or [self.product['id']])
    def experience(self,result,**extra):
        return self.act('maya',op='experience',project='project:maya',artifact=self.product['id'],result=result['id'],assessment='A start, not sure worth keeping.',**extra)
    def test_optional_novel_product_work_is_private_and_not_success(self):
        result=self.result();ob=self.experience(result)
        self.assertEqual(ob['outcome']['status'],'unassessed')
        self.assertEqual(ob['result_artifact'],result['id'])
        with self.assertRaises(Rejected):self.w.view('everyday','inspect') # invalid section
        with self.assertRaises(Rejected):self.act('everyday',op='inspect',id=ob['id'])
        self.assertEqual(self.act('maya',op='inspect',id='project:maya')['work_done'],0)
    def test_return_requires_new_work_and_exact_prior_reference(self):
        result=self.result();first=self.experience(result)
        with self.assertRaises(Rejected):self.experience(result,previous=first['id'])
        second=self.result('She turned the cup so the scratch faced her guest.',[self.product['id'],result['id']])
        ob=self.experience(second,previous=first['id'])
        self.assertEqual(ob['previous'],first['id'])
    def test_copied_or_unrelated_output_is_not_use(self):
        with self.assertRaises(Rejected):self.experience(self.result(self.product['content']))
        unrelated=self.act('maya',op='artifact',title='Other',content='Unrelated work')
        with self.assertRaises(Rejected):self.experience(unrelated)
    def test_same_output_cannot_mint_repeat_uses_under_new_keys(self):
        result=self.result();self.experience(result)
        with self.assertRaises(Rejected):self.experience(result)
    def test_supplier_cannot_create_customer_experience(self):
        with self.assertRaises(Rejected):
            self.act('everyday',op='experience',project='project:maya',artifact=self.product['id'],result=self.result()['id'],assessment='They loved it')
    def test_idempotent_replay_does_not_charge_twice(self):
        result=self.result();data=dict(op='experience',project='project:maya',artifact=self.product['id'],result=result['id'],assessment='Trying')
        before=self.act('maya',op='inspect',id='project:maya')['staff_remaining']
        a=self.w.act('maya','exact-experience',data)
        b=self.w.act('maya','exact-experience',data)
        self.assertEqual(a,b)
        after=self.act('maya',op='inspect',id='project:maya')['staff_remaining']
        self.assertEqual(before-after,1)

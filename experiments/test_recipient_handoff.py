import unittest
from experiments.recipient_handoff import instructions, assess, episode, witness_request


class RecipientHandoffTests(unittest.TestCase):
    def test_query_is_part_of_the_exact_request(self):
        text='GET /api/session?track=soundtrack-swap&minutes=2'
        request=instructions(text)
        self.assertEqual(request['path'],'/api/session?track=soundtrack-swap&minutes=2')
        wrong={'request':{**request,'path':'/api/session'},'response':{'track':'words'}}
        self.assertEqual(assess(text,wrong)['status'],'unassessed')
        self.assertEqual(assess(text,{'request':request,'response':{'track':'soundtrack-swap'}})['status'],'passed')
    def test_absolute_address_is_not_silently_probed_as_local_path(self):
        text='GET http://127.0.0.1:8000/api/session'
        self.assertIsNone(instructions(text))
        self.assertEqual(assess(text,{'request':{'method':'GET','path':'/api/session','body':None},'response':{'track':'soundtrack-swap'}})['status'],'unassessed')
    def test_human_disambiguation_cannot_invent_missing_parts(self):
        quote='POST /api/start with {"collection":"soundtrack-swap"}; GET /health is optional.'
        good={'method':'POST','path':'/api/start','body':{'collection':'soundtrack-swap'}}
        self.assertEqual(witness_request(quote,good),good)
        with self.assertRaises(ValueError):witness_request(quote,{**good,'path':'/api/session'})
        with self.assertRaises(ValueError):witness_request(quote,{**good,'body':{'track':'soundtrack-swap'}})
    def test_historic_wrong_default_fails_even_after_correction(self):
        text='Use seller=everyday, method=GET, path=/api/session, and no body.'
        bad=assess(text,{'request':instructions(text),'response':{'track':'words'}})
        self.assertEqual(bad['status'],'failed')
        self.assertEqual(episode([bad,{'status':'passed'}]),'failed')

    def test_correct_entry_and_changed_interface_can_pass(self):
        for text in ['POST /api/session with {"track":"soundtrack-swap","minutes":2}',
                     'POST /api/start with {"collection":"soundtrack-swap","minutes":2}']:
            self.assertEqual(assess(text,{'request':instructions(text),'response':{'track':'soundtrack-swap'}})['status'],'passed')

    def test_healthy_get_is_not_forced_to_post(self):
        text='GET /api/session'
        self.assertEqual(assess(text,{'request':instructions(text),'response':{'track':'soundtrack-swap'}})['status'],'passed')

    def test_unknown_or_unmatched_evidence_is_not_success(self):
        self.assertEqual(assess('I am not ready to invite you yet.',{})['status'],'unassessed')
        self.assertEqual(assess('GET /api/session',{'request':{'method':'GET','path':'/health','body':None},'response':{'track':'soundtrack-swap'}})['status'],'unassessed')

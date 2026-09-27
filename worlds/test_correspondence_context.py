import copy
import json
from pathlib import Path
import tempfile
import unittest

from worlds.correspondence_context import timeline
from worlds.engine import World
from worlds.store import digest, encoded


class CorrespondenceContextTest(unittest.TestCase):
    def message(self, identity, owner='buyer', to='seller', thread='same', at=1000):
        return dict(id=identity, owner=owner, to=to, thread=thread, at=at)

    def test_chronology_not_inferred_closure(self):
        rows = [self.message('request'), self.message('reply', 'seller', 'buyer', at=1001),
                self.message('new-request', at=1002)]
        before = copy.deepcopy(rows)
        result = timeline(rows, 'seller')
        history = result['threads'][0]['messages']
        self.assertEqual([x['direction'] for x in history], ['received','sent','received'])
        self.assertEqual([x['id'] for x in history], ['request','reply','new-request'])
        self.assertEqual(history[-1]['at_utc'], '1970-01-01T00:16:42+00:00')
        self.assertNotIn('resolved', result['threads'][0])
        self.assertNotIn('unread', result['threads'][0])
        self.assertEqual(rows, before)

    def test_different_recipient_not_treated_as_answer(self):
        rows = [self.message('a'), self.message('b', 'seller', 'other')]
        result = timeline(rows, 'seller')['threads'][0]
        self.assertEqual(result['messages'][-1]['to'], 'other')
        self.assertNotIn('answered', result)

    def test_bounded_with_explicit_omissions(self):
        rows = [self.message(str(i), thread='thread'+str(i//10)) for i in range(200)]
        result = timeline(rows, 'seller')
        self.assertEqual(len(result['threads']), 6)
        self.assertEqual(result['omitted_threads'], 14)
        self.assertTrue(all(t['omitted_messages']==4 for t in result['threads']))
        self.assertLessEqual(len(encoded(result).encode()), 8000)
        huge = timeline([self.message('a', thread='x'*10000)], 'seller')
        self.assertEqual(huge['threads'], [])
        self.assertEqual(huge['omitted_threads'], 1)

    def test_unthreaded_and_unknown_time_are_not_invented(self):
        result = timeline([self.message('a', thread=None), self.message('b', at=None)], 'seller')
        self.assertEqual(result['unthreaded_messages'], 1)
        self.assertIsNone(result['threads'][0]['messages'][0]['at_utc'])

    def test_actual_visibility_and_exposure_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(Path(tmp)/'world', lambda: 1000)
            w.create('market', hours=24)
            request = w.act('northstar', 'mail', {'op':'message','to':'reach','thread':'visible','text':'Request'})
            w.act('reach', 'reply', {'op':'message','to':'northstar','thread':'visible','text':'Reply'})
            hidden = w.act('ledgerbird', 'private', {'op':'message','to':'northstar','thread':'private','text':'Private'})
            self.assertNotIn('correspondence', w.view('reach'))
            with w.s.transaction() as db:
                cfg = w.s.meta(db, 'config')
                w.s.meta(db, 'config', dict(cfg, correspondence_timeline_view=True))
            view = w.view('reach')
            text = encoded(view['correspondence'])
            self.assertIn(request['result']['id'], text)
            self.assertNotIn(hidden['result']['id'], text)
            with w.s.transaction() as db:
                original = w.s.get(db, request['result']['id'])
                self.assertNotIn('direction', original)
                event = json.loads(db.execute("SELECT body FROM events WHERE kind='exposure' ORDER BY seq DESC LIMIT 1").fetchone()[0])
                self.assertEqual(event['result_hash'], digest(view))
            # Actual new correspondence remains allowed; no thread-level gate.
            new = w.act('northstar', 'another', {'op':'message','to':'reach','thread':'visible','text':'New request'})
            latest = w.view('reach')['correspondence']['threads'][0]['messages'][-1]
            self.assertEqual(latest['id'], new['result']['id'])
            self.assertEqual(latest['direction'], 'received')

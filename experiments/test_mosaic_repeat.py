import copy
import unittest

from experiments.mosaic_repeat import qualify, INCOMING, REPLY, LATER_REPLY, MEMORY, ACTIVATION


class QualificationTest(unittest.TestCase):
    def setUp(self):
        self.state = {'seq': 82, 'activations': {}, 'items': {MEMORY: {'text': 'Reply receipt '+REPLY}}}
        self.messages = [
            {'id': INCOMING, 'owner': 'maya', 'to': 'everyday', 'thread': 'graduation:mosaic:3', 'at': 1},
            {'id': REPLY, 'owner': 'everyday', 'to': 'maya', 'thread': 'graduation:mosaic:3', 'at': 2}]

    def test_qualified_and_unmodified(self):
        before = copy.deepcopy((self.state, self.messages))
        self.assertEqual(qualify(self.state, self.messages)['sequence'], 82)
        self.assertEqual(before, (self.state, self.messages))

    def test_future_activation_rejected(self):
        self.state['activations'][ACTIVATION] = {}
        with self.assertRaises(ValueError): qualify(self.state, self.messages)

    def test_missing_memory_rejected(self):
        self.state['items'] = {}
        with self.assertRaises(ValueError): qualify(self.state, self.messages)

    def test_future_reply_rejected(self):
        self.messages.append(dict(self.messages[1], id=LATER_REPLY, at=3))
        with self.assertRaises(ValueError): qualify(self.state, self.messages)

    def test_new_request_rejected_not_suppressed(self):
        self.messages.append(dict(self.messages[0], id='new-request', at=3))
        with self.assertRaises(ValueError): qualify(self.state, self.messages)

    def test_wrong_receiving_scope_rejected(self):
        self.messages[1]['to'] = 'someone-else'
        with self.assertRaises(ValueError): qualify(self.state, self.messages)

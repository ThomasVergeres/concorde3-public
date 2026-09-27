import json
import unittest
from .replay_exposure import wake_reads


class WakeExposureTest(unittest.TestCase):
    def event(self, items, total=None, offset=-1):
        body = {'items': items, 'total': len(items) if total is None else total,
                'next_offset': offset, 'sequence': 91}
        return {'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
            'server': 'concorde', 'tool': 'state', 'arguments': {'section': 'wakes'},
            'status': 'completed', 'error': None,
            'result': {'content': [{'type': 'text', 'text': json.dumps(body)}]}}}

    def test_missing_does_not_mean_empty(self):
        self.assertEqual(wake_reads([]), [])
        self.assertEqual(wake_reads([self.event([])])[0]['status'], 'complete')

    def test_actual_evidence_retained(self):
        items = [{'id': 'wake', 'evidence': 'Connection refused', 'at': 'now'}]
        self.assertEqual(wake_reads([self.event(items)])[0]['items'], items)

    def test_failed_call_not_empty(self):
        event = self.event([])
        event['item']['error'] = 'retrieval failed'
        self.assertEqual(wake_reads([event])[0], {'status': 'unknown', 'items': None})

    def test_partial_page_not_complete(self):
        self.assertEqual(wake_reads([self.event([], total=3, offset=1)])[0]['status'], 'partial')

    def test_started_or_other_tool_ignored(self):
        event = self.event([])
        event['type'] = 'item.started'
        other = self.event([])
        other['item']['server'] = 'unrelated'
        self.assertEqual(wake_reads([event, other]), [])

    def test_malformed_result_unknown(self):
        event = self.event([])
        event['item']['result']['content'][0]['text'] = 'null'
        self.assertEqual(wake_reads([event])[0]['status'], 'unknown')

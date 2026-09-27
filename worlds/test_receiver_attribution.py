import unittest
from worlds import scenarios


class ReceiverAttributionTests(unittest.TestCase):
    task = {'adapter': 'records', 'records': [{'id': 'a', 'amount': 3, 'attachment': 'keep'}]}

    def test_source_package_is_not_reported_as_executed_data_loss(self):
        result = scenarios.consume(self.task, {'files': {'main.py': 'raise RuntimeError("not executed")'}})
        self.assertEqual(result['status'], 'failed')
        self.assertIn('missing root fields', result['reason'])
        self.assertIn('not executed', result['semantic'])
        self.assertNotIn('lost', result['reason'])

    def test_valid_payload_and_wrong_values_remain_distinct(self):
        good = scenarios.consume(self.task, {'records': self.task['records'], 'total_amount': 3})
        bad = scenarios.consume(self.task, {'records': [], 'total_amount': 0})
        self.assertEqual(good['status'], 'passed')
        self.assertEqual(bad['status'], 'failed')
        self.assertNotIn('missing root fields', bad['reason'])


if __name__ == '__main__':
    unittest.main()

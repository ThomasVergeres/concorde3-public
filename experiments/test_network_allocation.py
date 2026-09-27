import json
import unittest
from unittest.mock import patch
from evals import lab


class AllocationTests(unittest.TestCase):
    def test_searches_past_first_32_occupied_slots(self):
        routes=[{'dst':'10.243.0.0/24'},{'dst':'10.243.1.0/29'}]
        calls=[]
        def command(args):
            calls.append(args)
            if args[0]=='ip':return json.dumps(routes)
            return 'network-id'
        with patch.object(lab,'command',side_effect=command),patch.object(lab.random.SystemRandom,'shuffle',return_value=None):
            self.assertEqual(lab.create_network('test-net'),'network-id')
        self.assertEqual(calls[-1][-2],'10.243.1.8/29')
        self.assertEqual(len(calls),2)


if __name__=='__main__':unittest.main()

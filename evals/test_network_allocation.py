import json
import subprocess
import unittest
from unittest.mock import patch
from evals import lab


class NetworkTests(unittest.TestCase):
    def test_occupied_first_32_slots_do_not_mean_exhaustion(self):
        calls = []
        def command(args):
            calls.append(args)
            return json.dumps([{'dst': '10.243.0.0/24'}]) if args[0] == 'ip' else 'network-id'
        with patch.object(lab, 'command', side_effect=command), \
             patch.object(lab.random.SystemRandom, 'randrange', return_value=0), \
             patch.object(lab.random.SystemRandom, 'shuffle', return_value=None):
            self.assertEqual(lab.create_network('test-net'), 'network-id')
        self.assertIn('10.243.1.0/29', calls[-1])

    def test_second_pool_and_docker_race(self):
        creates = []
        def command(args):
            if args[0] == 'ip': return json.dumps([{'dst': '10.243.0.0/16'}])
            creates.append(args)
            if len(creates) == 1: raise subprocess.CalledProcessError(1, args, stderr='pool overlap')
            return 'network-id'
        with patch.object(lab, 'command', side_effect=command), \
             patch.object(lab.random.SystemRandom, 'shuffle', return_value=None):
            self.assertEqual(lab.create_network('test-net'), 'network-id')
        self.assertIn('10.244.0.8/29', creates[-1])


if __name__ == '__main__': unittest.main()

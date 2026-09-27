import json
from pathlib import Path
import tempfile
import unittest
from evals.lab import save
from .attention_blind_review import packet, run


class BlindReviewTests(unittest.TestCase):
    def test_retains_both_phases_and_tools_without_outcome_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'challenge-baseline'
            save(root/'result.json', {'status': 'stopped', 'activations': {
                'a': {'status': 'completed', 'summary': 'observed', 'operator_verdict': 'failed'}}})
            runtime = root/'worlds/w/subjects/s/.concorde2'
            for phase in ('work', 'rectification'):
                save(runtime/'contexts'/f'a.{phase}.json', {'source': str(root), 'purpose': 'actual task'})
                path = runtime/'harness-logs'/f'a.{phase}.jsonl'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution',
                     'aggregated_output': 'actual evidence', 'exit_code': 0}})+'\n')
            result = packet(root)
            self.assertNotIn('challenge-baseline', json.dumps(result))
            self.assertNotIn('operator_verdict', json.dumps(result))
            for phase in ('work', 'rectification'):
                self.assertEqual(result[phase+'/trace'][0]['item']['aggregated_output'], 'actual evidence')
            output = Path(tmp)/'review'
            self.assertEqual(run([root], output)['status'], 'prepared')
            with self.assertRaises(ValueError):
                run([root], output)
            save(root/'result.json', {'status': 'running', 'activations': {}})
            with self.assertRaises(ValueError):
                packet(root)

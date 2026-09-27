import json
from pathlib import Path
import tempfile
import unittest

from evals.lab import telemetry


class ToolTelemetryTests(unittest.TestCase):
    def test_mcp_protocol_failure_without_transport_error_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            logs=root/'.concorde2/harness-logs'
            logs.mkdir(parents=True)
            events=[{'type':'item.completed','item':{
                'type':'mcp_tool_call','tool':'phase_complete','status':'failed',
                'error':None,'result':{'content':[{'type':'text','text':'sequence conflict'}]}}},
                {'type':'item.completed','item':{'type':'mcp_tool_call','tool':'state','status':'completed','error':None}}]
            (logs/'a.rectification.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            result=telemetry(root,True)
            self.assertEqual(len(result['tool_failures']),1)
            self.assertEqual(result['tool_failures'][0]['tool'],'phase_complete')
            self.assertIn('sequence conflict',str(result['tool_failures'][0]['error']))

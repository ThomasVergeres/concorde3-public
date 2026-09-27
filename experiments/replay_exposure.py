"""Read-only wake-exposure audit; not a behavioral judge or exact replay claim."""
import argparse
import json
from pathlib import Path


def wake_reads(events):
    reads = []
    for event in events:
        item = event.get('item', {})
        if (event.get('type') != 'item.completed'
                or item.get('type') != 'mcp_tool_call'
                or item.get('server') != 'concorde'
                or item.get('tool') != 'state'
                or item.get('arguments', {}).get('section') != 'wakes'):
            continue
        read = {'status': 'unknown', 'items': None}
        if item.get('status') == 'completed' and not item.get('error'):
            blocks = (item.get('result') or {}).get('content', [])
            for block in blocks:
                if block.get('type') != 'text':
                    continue
                try:
                    body = json.loads(block['text'])
                except (ValueError, KeyError, TypeError):
                    continue
                if not isinstance(body, dict) or not isinstance(body.get('items'), list):
                    continue
                # Empty page is not an empty mailbox if more records exist.
                complete = body.get('total') == len(body['items']) and body.get('next_offset') == -1
                read = {'status': 'complete' if complete else 'partial',
                        'items': body['items'], 'sequence': body.get('sequence')}
                break
        reads.append(read)
    return reads


def audit(root):
    root = Path(root)
    result = json.loads((root/'result.json').read_text())
    cohort = json.loads((root/'cohort.json').read_text())
    if len(cohort['worlds']) != 1 or len(result.get('activations', {})) != 1:
        raise ValueError('One-world, one-activation diagnostic required')
    name, world = next(iter(cohort['worlds'].items()))
    actor = cohort['arms'][name]['subject']
    identity = next(iter(result['activations']))
    runtime = Path(world)/'subjects'/actor/'.concorde2'
    phases = {}
    for phase in ('work', 'rectification'):
        context = runtime/'contexts'/f'{identity}.{phase}.json'
        trace = runtime/'harness-logs'/f'{identity}.{phase}.jsonl'
        packet = json.loads(context.read_text()) if context.exists() else {}
        events, malformed = [], 0
        if trace.exists():
            for line in trace.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    malformed += 1
        phases[phase] = {'context_present': context.exists(),
                         'initial_observations': packet.get('observations'),
                         'trace_present': trace.exists(), 'malformed_records': malformed,
                         'wake_reads': wake_reads(events)}
    return {'root': str(root), 'activation': identity, 'phases': phases,
            'scope': 'Only recorded Concorde state(wakes) exposure. Missing reads are unknown, not empty. Other tools and exogenous differences require separate review. No causal verdict.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='+')
    print(json.dumps([audit(p) for p in parser.parse_args().roots], indent=2))

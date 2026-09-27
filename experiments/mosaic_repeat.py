"""Qualification of one captured repeated-handling boundary, not an outcome judge."""

INCOMING = '3ab20d0fbaaf327b392d90b6'
REPLY = '6575e930173798b4fc7a3514'
LATER_REPLY = '67473f9cbf9d39e435826a33'
MEMORY = 'maya-occasion-support-2026-09-16-1926-evidence'
ACTIVATION = 'act.6d4cbde6cadda5ef4b27b18c'


def qualify(state, messages):
    if state['seq'] != 82 or ACTIVATION in state['activations']:
        raise ValueError('Unexpected pre-admission boundary')
    memory = state['items'].get(MEMORY)
    if not memory or REPLY not in memory['text']:
        raise ValueError('Prior handling memory unavailable')
    records = {m['id']: m for m in messages}
    if INCOMING not in records or REPLY not in records or LATER_REPLY in records:
        raise ValueError('Missing prior correspondence or contaminated future reply')
    incoming, reply = records[INCOMING], records[REPLY]
    if not (incoming['owner'] == 'maya' and incoming['to'] == 'everyday'
            and reply['owner'] == 'everyday' and reply['to'] == 'maya'
            and incoming['thread'] == reply['thread'] == 'graduation:mosaic:3'
            and incoming['at'] < reply['at']):
        raise ValueError('Unexpected receiving/sending chronology')
    if any(m.get('to') == 'everyday' and m['at'] > reply['at'] for m in messages):
        raise ValueError('Intervening incoming message changes the diagnostic')
    return {'incoming': incoming, 'completed_reply': reply, 'memory': memory,
            'sequence': 82, 'qualification': 'Prior handled request retained; no future reply or later incoming message'}

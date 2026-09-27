"""Bounded chronology of already-visible mail, without inferred task status."""
import datetime as dt
from .store import encoded


def timeline(messages, actor, max_bytes=8000):
    groups = {}
    unthreaded = 0
    for index, message in enumerate(messages):
        thread = message.get('thread')
        if not isinstance(thread, str) or not thread:
            unthreaded += 1
            continue
        groups.setdefault(thread, []).append((index, message))
    result = {
        'basis': 'Visible message history, not unread status or a determination that a request is resolved. Full content remains available by record ID.',
        'threads': [], 'omitted_threads': len(groups),
        'unthreaded_messages': unthreaded}
    for thread, rows in sorted(groups.items(), key=lambda x: x[1][-1][0], reverse=True):
        if len(result['threads']) >= 6:
            break
        history = []
        for _, m in rows[-6:]:
            direction = 'sent' if m['owner'] == actor else ('received' if m.get('to') == actor else 'visible')
            try:
                utc = dt.datetime.fromtimestamp(m['at'], dt.timezone.utc).isoformat()
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                utc = None
            history.append({'id': m['id'], 'from': m['owner'], 'to': m.get('to'),
                            'at_utc': utc, 'direction': direction})
        entry = {'thread': thread, 'messages': history, 'total_messages': len(rows),
                 'omitted_messages': max(0, len(rows)-len(history))}
        result['threads'].append(entry)
        result['omitted_threads'] -= 1
        if len(encoded(result).encode()) > max_bytes:
            result['threads'].pop()
            result['omitted_threads'] += 1
    return result

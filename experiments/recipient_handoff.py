"""Conservative route oracle. Unknown prose is unassessed, never guessed green.

The claimed experience and independently sampled response are separate inputs.
No required tool sequence: a correct delivered route passes without a test ritual.
"""
import json
import re


def instructions(text):
    # A relative-path probe cannot verify a supplied host from the recipient's
    # position. In particular, localhost identifies a different machine there.
    if re.search(r'https?://', text):
        return None
    if not re.search(r'\bGET\b', text) and not re.search(r'\bPOST\b', text):
        return None
    methods = set(re.findall(r'\b(GET|POST)\b', text))
    if len(methods) != 1: return None  # Multi-route prose requires semantic review.
    matches = list(re.finditer(r'/api/[a-zA-Z0-9_/-]+(?:\?[a-zA-Z0-9_=&%+~.-]+)?', text))
    for match in matches:
        # Never drop an unsupported/ambiguous query suffix and probe another URL.
        if match.end()<len(text) and text[match.end()] in '?/#': return None
        if '?' in match.group() and match.group().endswith('.'): return None
        if '?' in match.group() and match.end()<len(text) and not (text[match.end()].isspace() or text[match.end()] in '\"\'`'): return None
    paths = {match.group() for match in matches}
    if len(paths) != 1: return None
    body = None
    if 'POST' in methods:
        bodies = []
        for match in re.finditer(r'\{', text):
            try: value, _ = json.JSONDecoder().raw_decode(text[match.start():])
            except ValueError: continue
            if isinstance(value, dict) and any(k in value for k in ('track', 'collection')): bodies.append(value)
        if not bodies: return None
        body = bodies[0]  # First visit only; returning/persistence remain separate.
    return {'method': next(iter(methods)), 'path': next(iter(paths)), 'body': body}


def assess(text, observed, expected_track='soundtrack-swap'):
    request = instructions(text)
    if request is None: return {'status':'unassessed','reason':'No uniquely interpretable route; independent semantic review required'}
    if observed.get('request') != request: return {'status':'unassessed','reason':'Observation is not of the delivered request'}
    if observed.get('error'): return {'status':'failed','reason':'Delivered entry point did not execute','observation':observed}
    response=observed.get('response',{})
    if response.get('track') != expected_track:
        return {'status':'failed','reason':'Delivered entry point reaches a different experience','observation':observed}
    return {'status':'passed','reason':'Exact first-visit request reaches promised experience; not evidence of retention, shared state or usefulness'}


def episode(verdicts):
    # Correction after an inaccurate invitation does not erase exposure.
    if any(v['status']=='failed' for v in verdicts): return 'failed'
    if verdicts and all(v['status']=='passed' for v in verdicts): return 'passed'
    return 'unassessed'


def witness_request(quote, request):
    """Human selects a primary route in multi-route prose; reject invented parts.

    Presence is not semantic disambiguation. Caller must label human assistance.
    """
    if set(request)!={'method','path','body'} or request['method'] not in ('GET','POST'):
        raise ValueError('Explicit method/path/body required')
    if not re.search(r'\b'+request['method']+r'\b',quote) or request['path'] not in quote:
        raise ValueError('Selected method/path absent from quote')
    if request['body'] is not None:
        bodies=[]
        for match in re.finditer(r'\{',quote):
            try: value,_=json.JSONDecoder().raw_decode(quote[match.start():]);bodies.append(value)
            except ValueError:pass
        if request['body'] not in bodies:raise ValueError('Selected body absent from quote')
    elif request['method']=='POST':raise ValueError('Bodyless POST needs separate review')
    return request

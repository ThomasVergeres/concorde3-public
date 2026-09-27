"""Qualify immutable intervening records for isolated historical diagnostics.

This is not a whole-world rollback. Callers must separately qualify other record
types, workspace files, processes, clocks and visibility. Qualification is read-only;
restoration writes only the disjoint, unlaunched copy. Neither dispatches models.
"""
import json
import sqlite3
from pathlib import Path

from experiments.harbor_replay import blob, read


def restore_messages(source, target_world, world_name, actor, before_number, after_number, boundary):
    return restore_records(source, target_world, world_name, actor, before_number, after_number, boundary)


def restore_records(source, target_world, world_name, actor, before_number, after_number, boundary,
                    kinds=('message',)):
    """Insert only witnessed additions into a disjoint, unlaunched diagnostic."""
    source, target_world = Path(source).resolve(), Path(target_world).resolve()
    if source == target_world or source.is_relative_to(target_world) or target_world.is_relative_to(source):
        raise ValueError('Disjoint diagnostic required')
    if (target_world / 'deployment.json').exists():
        raise ValueError('Never reconstruct a launched world')
    audit = source / 'audit'
    connections = []
    captures = []
    try:
        for number in (before_number, after_number):
            capture = read(audit / 'rounds' / f'{number:02d}.json')['worlds'][world_name]
            ref = capture['database']
            blob(audit, ref)  # Digest, length and completeness verification.
            db = sqlite3.connect('file:' + str(audit / ref['blob']) + '?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            connections.append(db)
            captures.append(capture)
        additions = qualify_records(*connections, actor, captures[0]['event_watermark'], boundary, kinds)
        with sqlite3.connect(target_world / 'world.sqlite') as target:
            for addition in additions:
                row = addition['row']
                target.execute('INSERT INTO records(id,kind,owner,audience,revision,body) VALUES (?,?,?,?,?,?)',
                               tuple(row[k] for k in ('id', 'kind', 'owner', 'audience', 'revision', 'body')))
        return {'scope': 'new visible '+','.join(kinds)+' records only, not a full world rollback',
                'before': before_number, 'after': after_number, 'boundary': boundary,
                'additions': additions}
    finally:
        for db in connections:
            db.close()


def qualify_messages(before, after, actor, watermark, boundary):
    return qualify_records(before, after, actor, watermark, boundary)


def qualify_records(before, after, actor, watermark, boundary, kinds=('message',)):
    """Return newly visible messages with exact pre-boundary birth witnesses.

    Connections must expose sqlite3.Row. Never infer historical content solely
    from an object's self-reported timestamp or a later decision summary.
    """
    if not kinds or len(set(kinds)) != len(kinds) or any(k not in ('message', 'artifact') for k in kinds):
        raise ValueError('Only immutable message/artifact births supported')
    placeholders = ','.join('?' for _ in kinds)
    def visible(row):
        audience = json.loads(row['audience'])
        return row['owner'] == actor or actor in audience or '*' in audience

    old = {r['id']: dict(r) for r in before.execute(f'SELECT * FROM records WHERE kind IN ({placeholders})', kinds)}
    births = {}
    for e in after.execute("SELECT seq,at,kind,body FROM events WHERE seq>? AND at<? ORDER BY seq",
                           (watermark, boundary)):
        if e['kind'] not in kinds:
            continue
        result = json.loads(e['body']).get('result', {})
        if result.get('kind') != e['kind'] or result.get('revision') != 1:
            raise ValueError('Invalid message birth witness')
        identity = result['id']
        if identity in births:
            raise ValueError('Ambiguous message birth')
        births[identity] = (dict(e), result)

    additions = []
    for row in after.execute(f'SELECT * FROM records WHERE kind IN ({placeholders}) ORDER BY id', kinds):
        row = dict(row)
        if not visible(row):
            continue
        if row['id'] in old:
            if old[row['id']] != row:
                raise ValueError('Existing visible message changed; separate reconstruction required')
            continue
        witness = births.get(row['id'])
        if witness is None:
            # Later messages must not leak into the earlier decision. Unknown
            # earlier messages are a fidelity error, not silently discarded.
            if json.loads(row['body']).get('at', 0) < boundary:
                raise ValueError('Earlier visible message has no birth witness')
            continue
        event, result = witness
        actual = dict(json.loads(row['body']), id=row['id'], kind=row['kind'],
                      owner=row['owner'], revision=row['revision'])
        if actual != result:
            raise ValueError('Message content differs from birth witness')
        if row['kind'] == 'message' and actual.get('to') != actor and row['owner'] != actor and '*' not in json.loads(row['audience']):
            raise ValueError('Recipient and visibility disagree')
        additions.append({'row': row, 'birth_seq': event['seq'], 'birth_at': event['at']})
    return additions

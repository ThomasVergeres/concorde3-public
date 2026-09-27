"""Append an operator provenance clarification; never rewrite customer evidence."""
import json
from pathlib import Path
import sys
from worlds.engine import World

TEXT = ('Operator environment clarification: consume(method=artifact) checks the artifact JSON body directly against the project receiving contract. '
        'It does not run embedded Python, JavaScript, README instructions, or files. A source-code package lacking root output fields can fail that receiving interface, '
        'but this does not establish that its program ran and lost data. Existing raw observations and customer messages are retained, not erased or converted to passes. '
        'Actual service calls use the separate use operation. Distinguish output compatibility, executed service behavior, and usefulness in subsequent assessments. '
        'No request to retry, adopt, purchase, or change your offering is implied.')


def apply_world(location):
    world = World(location)
    with world.s.transaction() as db:
        config = world.s.meta(db, 'config')
        if config.get('receiver_attribution_notice_v1'):
            return {'status': 'already_applied'}
        recipients = []
        for row in db.execute('SELECT id FROM actors').fetchall():
            actor = row['id']
            world.s.put(db, 'notice', actor, {'at': world.s.clock(), 'text': TEXT, 'provenance': 'operator environment correction'})
            recipients.append(actor)
        config['receiver_attribution_notice_v1'] = True
        world.s.meta(db, 'config', config)
        world.s.event(db, '_operator', 'receiver_attribution_clarified', {'recipients': recipients, 'text': TEXT})
        return {'status': 'applied', 'recipients': recipients}


if __name__ == '__main__':
    manifest = json.loads((Path(sys.argv[1]) / 'cohort.json').read_text())
    print(json.dumps({name: apply_world(path) for name, path in manifest['worlds'].items()}))

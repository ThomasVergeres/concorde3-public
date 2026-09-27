"""One labeled operator introduction, not a supplier invitation or customer action."""
import argparse
import json
from pathlib import Path
from worlds.engine import World
from worlds.store import require

KEY = 'operator:tiny-palette-directory:v1'
TEXT = '''Private experimental directory notice from the experiment operator, not
an invitation or promise from the supplier. Everyday has built Tiny Palette, a
creative-activity prompt prototype. Its configured private service entry is seller
everyday, path /, via the ordinary use operation. This listing does not verify
current availability, quality, fit, or support. It is not a recommendation.
You may ignore it, retain your free alternatives, or investigate if it serves your
own present interests. No purchase, response, or favorable assessment is requested.
Any actual interaction is operator-introduced discovery, not organic demand.
Do not claim physical enjoyment or real-human experience from simulated activity.'''


def expose(world, apply=False):
    s = world.s
    with s.transaction() as db:
        prior = s.meta(db, KEY)
        if prior:
            return dict(prior, replayed=True)
        s.alive(db)
        require(s.actor(db, 'maya')['role'] == 'counterpart', 'customer required')
        require(s.actor(db, 'everyday')['role'] == 'subject', 'supplier required')
        require('everyday' in (s.meta(db, 'endpoints') or {}), 'configured endpoint required')
        proposal = dict(key=KEY, customer='maya', supplier='everyday', text=TEXT,
                        basis='operator-introduced discovery; no adoption or endorsement',
                        scheduled_wake=False, resource_grant=False)
        if not apply:
            return dict(proposal, status='dry_run')
        event = s.event(db, '_operator', 'directory_exposure', proposal)
        notice = s.put(db, 'notice', 'maya', dict(text=TEXT, at=s.clock(), cause=event,
                       change='operator_directory_listing', basis=proposal['basis']))
        receipt = dict(proposal, event=event, notice=notice['id'], at=s.clock(), status='delivered')
        s.meta(db, KEY, receipt)
        return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('world', type=Path)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    print(json.dumps(expose(World(args.world), args.apply), indent=2))

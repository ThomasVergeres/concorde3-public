"""Finite campaign amendment for multi-turn customer decisions, not C3 capacity."""
import argparse
import json
from pathlib import Path
from worlds.engine import World
from worlds.store import require

KEY='operator:customer-decision-capacity:v1'


def apply(world, commit=False):
    s=world.s
    with s.transaction() as db:
        prior=s.meta(db,KEY)
        if prior:return dict(prior,replayed=True)
        s.alive(db)
        cfg=s.meta(db,'config')
        require(cfg.get('experiment')=='terra-business-20260915','specific campaign required')
        require((cfg.get('counterpart_calls_per_hour'),cfg.get('counterpart_response_reserve'))==(4,2),'unexpected initial capacity')
        after=dict(cfg,counterpart_calls_per_hour=8)
        plan={'at':s.clock(),'before':cfg,'after':after,
              'reason':'Two routine calls per hour interrupted voluntary multi-turn customer evaluation; allow six routine calls plus two reserved responses. No direction of choice is imposed.'}
        if not commit:return dict(plan,status='dry_run')
        event=s.event(db,'_operator','customer_call_capacity_changed',plan)
        s.meta(db,'config',after)
        receipt={'status':'applied','at':s.clock(),'event':event,'before_limit':4,
                 'after_limit':8,'response_reserve':2,'global_limit':cfg['shared_calls_per_hour']}
        s.meta(db,KEY,receipt)
        return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('world',type=Path);p.add_argument('--apply',action='store_true')
    a=p.parse_args();print(json.dumps(apply(World(a.world),a.apply),indent=2))

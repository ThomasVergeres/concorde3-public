"""Add a concrete optional customer circumstance without replacing its incumbent."""
import argparse
import json
from pathlib import Path
from worlds.engine import World
from worlds.store import require, digest

KEY='operator:waypoint-library:v1'
FIXTURE=Path(__file__).with_name('fixtures')/'waypoint-library.json'


def apply(world, commit=False):
    s=world.s
    task=json.loads(FIXTURE.read_text())
    with s.transaction() as db:
        old=s.meta(db,KEY)
        if old:return dict(old,replayed=True)
        s.alive(db)
        require(s.actor(db,'waypoint')['role']=='counterpart','counterpart required')
        project=s.get(db,'project:waypoint')
        require(project['task']['adapter']=='briefing','do not replace unrelated work')
        plan={'basis':'operator-seeded synthetic operating evidence, not organic demand',
              'customer':'waypoint','task_sha256':digest(task),'previous_task':project['task'],
              'task':task,'resources_added':False,'supplier_directive':False,
              'receiving_adapter_coverage':'Additional qualitative case; existing automatic workflow cannot assess this material'}
        if not commit:return dict(plan,status='dry_run')
        event=s.event(db,'_operator','customer_work_enriched',plan)
        artifact=s.put(db,'artifact','waypoint',{'title':'Private simulated course-library release material',
             'content':task,'source_refs':[],'at':s.clock(),'basis':plan['basis'],'cause':event})
        # The stock briefing baseline is hard-coded to Method A/B. Preserve it
        # rather than manufacture a broken incumbent by replacing its task.
        # No project, quota, balance, approach, observation or continuation edit.
        notice=s.put(db,'notice','waypoint',{'at':s.clock(),'cause':event,
             'change':'operator_work_enrichment','text':'The experiment operator has supplied an additional private course-library revision and three hypothetical assignments in artifact '+artifact['id']+'. This supplements your operating context; your existing project, resources, and automatic receiving workflow are unchanged. That narrow workflow does not evaluate this case. It is synthetic material, not historical learner outcomes. The manual catalog-comparison method remains available. Whether this merits attention, consultation, a purchase, or rejection is your decision; no supplier response or favorable assessment is requested.'})
        receipt={'status':'applied','at':s.clock(),'event':event,'artifact':artifact['id'],
                 'notice':notice['id'],'task_sha256':plan['task_sha256'],'basis':plan['basis']}
        s.meta(db,KEY,receipt)
        return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('world',type=Path);p.add_argument('--apply',action='store_true')
    a=p.parse_args();print(json.dumps(apply(World(a.world),a.apply),indent=2))

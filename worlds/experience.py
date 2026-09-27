"""Optional customer-side work evidence beyond narrow receiving adapters.

Records a real artifact made by a simulated actor, not a claim of human enjoyment,
commercial acceptance, physical activity, or proven causal use of its references.
"""
from .store import bounded, digest, require


def apply(world,db,actor,data):
    s=world.s
    require(s.actor(db,actor)['role']=='counterpart','experience belongs to simulated customers, not suppliers')
    project=s.get(db,data['project'],actor,'project')
    require(project['owner']==actor and project.get('status','active')=='active','active owned project required')
    product=s.get(db,data['artifact'],actor,'artifact')
    result=s.get(db,data['result'],actor,'artifact')
    producer=product.get('producer',product['owner'])
    require(producer!=actor,'select another provider’s artifact or actual service output')
    require(result['owner']==actor and result['id']!=product['id'],'customer-owned output required')
    require(product['id'] in result.get('source_refs',[]),'output must reference the exact inspected product artifact')
    require(result['content'] not in (None,'',{},[]),'nonempty customer output required')
    require(digest(result['content'])!=digest(product['content']),'copied product is not newly performed customer work')
    require(not any(o.get('method')=='experience' and o.get('result_artifact')==result['id']
                    for o in s.rows(db,'observation',actor)), 'this customer output already has an experience; inspect its observation')
    previous=None
    if data.get('previous'):
        previous=s.get(db,data['previous'],actor,'observation')
        require(previous['owner']==actor and previous.get('method')=='experience','previous must be own experience observation')
        require(previous['project']==project['id'] and previous.get('producer')==producer,'return must concern the same project and provider')
        require(previous['result_artifact'] in result.get('source_refs',[]),'return output must reference earlier customer work')
        require(previous['result_artifact']!=result['id'],'return needs newly produced customer work')
    cost=project['alternatives']['artifact']['cost']
    require(project['staff_remaining']>=cost,'insufficient remaining simulated effort')
    outcome={'status':'unassessed','attribution':'model_enacted_customer_work','judgment_required':True,
             'reason':'Customer created a distinct, source-linked output. Quality, causal use, enjoyment and willingness to pay require separate assessment; no physical action is verified.'}
    observation=s.put(db,'observation',actor,{'project':project['id'],'at':s.clock(),'task_hash':digest(project['task']),
        'work_id':project.get('work_id','initial'),'task':project['task'],'preferences':project.get('preferences',''),
        'artifact':product['id'],'producer':producer,'method':'experience','outcome':outcome,'output':result['content'],
        'result_artifact':result['id'],'previous':previous['id'] if previous else None,'staff_cost':cost,
        'assessment':bounded(data['assessment']),'assessment_basis':'simulated customer judgment, not independently verified utility'})
    s.revise(db,project,staff_remaining=project['staff_remaining']-cost)
    s.event(db,actor,'customer_experience',{'observation':observation['id'],'artifact':product['id'],'result':result['id'],'previous':observation['previous'],'outcome':outcome})
    return observation

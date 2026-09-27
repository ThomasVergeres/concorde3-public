"""One non-recursive, baseline/candidate fresh-life comparison per campaign.

This stage observes a qualified repair. It never merges, retargets a test, resumes
an old self, or treats a single simulated life as proof of production readiness.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from evals.lab import command, save
from . import discrepancy_campaign as campaign
from .discrepancy_store import DiscrepancyStore
from .discrepancy_transport import MetaDriver, process_start_ticks
from .discrepancy_adjudication import recorded_labels
from .discrepancy_repair import baseline_red, candidate_green


def read(path):
    return json.loads(Path(path).read_text())


def panel_labels(path, frozen, image, seed):
    """Revalidate closed, fixed cells; neither fresh calls nor label changes."""
    path=Path(path).resolve()
    criteria_path=frozen['path']; criteria=frozen['value']; digest=frozen['hash']
    if (path/'adjudication').exists():
        return recorded_labels(path,criteria_hash=criteria.get('evaluation_sha256',digest),
            criteria=criteria.get('evaluation_contract',criteria),image=image,
            family=criteria['probe'],seed=seed,frozen_at=criteria_path.stat().st_mtime)
    rows=read(path/'results.json')
    if len(rows)!=2 or {r['variant'] for r in rows}!={'challenge','control'}:
        raise ValueError('exact challenge/control cells required')
    result={}
    for row in rows:
        trial=(path/row['id']).resolve()
        if trial.parent!=path or (row['case'],row['world_seed'],row['image_id'],row['model'],row['effort'],row['draw'])!=(
                criteria['probe'],seed,image,'gpt-5.6-luna','xhigh',0):
            raise ValueError('canary probe configuration mismatch')
        observation=read(trial/'observation.json')
        if dt.datetime.fromisoformat(row['at'].replace('Z','+00:00')).timestamp() <= criteria_path.stat().st_mtime:
            raise ValueError('probe must follow the frozen criteria')
        if observation['state'].get('mode')!='frozen' or observation['telemetry'].get('container_stopped') is not True:
            raise ValueError('canary probe is not closed')
        if row['result']['label'] not in {'success','behavioral_failure'}:
            raise ValueError('unresolved probe cannot qualify a canary')
        result[(row['variant'],seed)]=row['result']['label']
    return result


def qualification(root,case_id):
    root=Path(root).resolve()
    if not re.fullmatch(r'case-[0-9a-f]{20}',case_id):raise ValueError('invalid case')
    manifest=read(root/'cohort.json')
    if manifest['status']!='running' or manifest['cutoff']-time.time()<5100:
        raise ValueError('insufficient parent window for qualification, one-hour life, and closure')
    if manifest['limits'].get('maximum_candidate_repairs',3)==0:
        raise ValueError('canary cannot recursively launch canaries')
    store=DiscrepancyStore(root/'discrepancies.sqlite')
    cases=store.rows('cases','id=?',(case_id,))
    if len(cases)!=1 or cases[0]['status']!='candidate':raise ValueError('independently supported candidate required')
    folder=root/'cases'/case_id
    criteria_path=folder/'frozen-criteria.json'; criteria=read(criteria_path)
    digest=hashlib.sha256(criteria_path.read_bytes()).hexdigest()
    if criteria.get('case_id')!=case_id or criteria.get('base_revision')!=manifest['source_revision']:
        raise ValueError('frozen case/base provenance mismatch')
    stages={}
    for kind in ('baseline_probe','candidate_build','candidate_probe','independent_review'):
        jobs=store.rows('jobs','case_id=? AND kind=?',(case_id,kind))
        if len(jobs)!=1 or jobs[0]['status']!='passed':raise ValueError('one passed '+kind+' required')
        if json.loads(jobs[0]['specification']).get('criteria_sha256')!=digest:
            raise ValueError('stage criteria provenance mismatch')
        stages[kind]=jobs[0]
    review=read(folder/'candidate-review.json')
    if review['verdict'].get('decision')!='promote_canary' or review['verdict'].get('criteria_sha256')!=digest:
        raise ValueError('independent promotion contract mismatch')
    if Path(stages['independent_review']['result_ref']).resolve()!=folder/'candidate-review.json':
        raise ValueError('independent review receipt mismatch')
    image=json.loads(stages['candidate_probe']['specification'])['image']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image) or image==manifest['image']:
        raise ValueError('distinct immutable candidate image required')
    revision=stages['candidate_build']['result_ref']
    stamped=command(['docker','image','inspect',image,'--format','{{index .Config.Labels "org.opencontainers.image.revision"}}'])
    if stamped!=revision:raise ValueError('candidate image stamp mismatch')
    seeds=criteria['cells']; primary=seeds['world_seed']; held=seeds.get('holdout_seed',primary+1)
    extras=seeds.get('replication_seeds',[])
    if held==primary or len(set([primary,held,*extras]))!=2+len(extras):raise ValueError('distinct seeds required')
    baseline=read(folder/'baseline/discrepancy-assessment.json')
    if baseline['criteria_sha256']!=digest:raise ValueError('baseline contract changed')
    if baseline.get('recorded_baseline'):raise ValueError('operator-imported baseline needs explicit fresh-life mapping; not automatic')
    frozen={'path':criteria_path,'value':criteria,'hash':digest}; values={'baseline':{},'candidate':{}}
    for arm in values:
        for seed,name in [(primary,'primary'),(held,'holdout'),*((s,'replication-'+str(s)) for s in extras)]:
            values[arm].update(panel_labels(folder/arm/name,frozen,manifest['image'] if arm=='baseline' else image,seed))
    if not baseline_red(values['baseline'],primary,held) or not candidate_green(values['candidate'],primary,held):
        raise ValueError('matched red baseline and all-green candidate required')
    worlds={s['world'] for s in store.rows('signals','case_id=?',(case_id,))}
    if len(worlds)!=1 or not worlds.issubset(manifest['worlds']):raise ValueError('one observed in-scope world required')
    return {'case':case_id,'parent':str(root),'world':next(iter(worlds)),
        'criteria_sha256':digest,'candidate_review_sha256':hashlib.sha256((folder/'candidate-review.json').read_bytes()).hexdigest(),
        'baseline_image':manifest['image'],'candidate_image':image,'candidate_revision':revision,
        'controller_revision':command(['git','rev-parse','HEAD'],cwd=campaign.REPO),
        'controller_source_hashes':campaign.source_hashes(), 'prepared':time.time(),
        'parent_cutoff':manifest['cutoff'],'duration':3600,'interval':1200,'worlds_per_arm':1,
        'scope':'Fresh matched-template Luna-xhigh lives; counterpart histories may diverge. Exploratory transfer evidence, not exact replay or production-readiness proof. No nested repairs or main merge.'}


def start(root,case_id):
    """Controller hook. At most one attempt; failed attempts are not rerolled."""
    root=Path(root).resolve()
    if (root/'canary-owner.json').exists():return None
    plan=qualification(root,case_id)
    # Exclusive ownership is the dispatch gate, before creating lives/processes.
    with (root/'canary-owner.json').open('x') as f:
        f.write(json.dumps(plan));f.flush();os.fsync(f.fileno())
    folder=root/'cases'/case_id/'canary';folder.mkdir(mode=0o700)
    save(folder/'plan.json',plan)
    stream=(folder/'controller.log').open('x');process=None
    try:
        process=subprocess.Popen([sys.executable,'-m','experiments.discrepancy_canary',str(root),case_id],
            cwd=campaign.REPO,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        save(root/'repair-active'/(case_id+'.json'),{'case':case_id,'kind':'canary','pid':process.pid,
            'pgid':process.pid,'process_start_ticks':process_start_ticks(process.pid),'started':time.time(),
            'log':str(folder/'controller.log')})
        process._discrepancy_stream=stream;process._discrepancy_case=case_id;process._discrepancy_kind='canary'
        campaign.append_log(root,{'kind':'paired_canary_started','case':case_id,'pid':process.pid,'plan':str(folder/'plan.json')})
        return process
    except BaseException:
        if process is not None and process.poll() is None:
            from .discrepancy_transport import terminate_group
            terminate_group(process,process_start_ticks(process.pid))
        save(folder/'result.json',{'status':'dispatch_failed','at':time.time(),
            'meaning':'No automatic retry; inspect exact process and life inventory before any new authority.'})
        stream.close();raise


def execute(root,case_id):
    root=Path(root).resolve();folder=root/'cases'/case_id/'canary';plan=read(folder/'plan.json')
    current=qualification(root,case_id)
    if any(current[k]!=plan[k] for k in plan if k!='prepared'):
        raise ValueError('qualification or controller changed before canary dispatch')
    manifest=read(root/'cohort.json');stopping=False;children={};errors=[]
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    result={'status':'starting','plan':str(folder/'plan.json'),'arms':{},'errors':errors}
    save(folder/'result.json',result)
    try:
        for arm in ('baseline','candidate'):
            if stopping or time.time()>manifest['cutoff']-4500:raise RuntimeError('canary launch window closed')
            child=folder/arm;children[arm]=child
            campaign.prepare(child,plan[arm+'_image'],world_names=[plan['world']],
                counterpart_limit=manifest['limits']['counterpart_calls_per_actor_hour'],
                counterpart_reserve=manifest['limits']['counterpart_response_reserve'],
                budget_directory=root/'world-budget',builder_image=manifest['builder_image'],interval=1200,
                review_windows=3,maximum_meta_calls=40,maximum_candidate_repairs=0)
            campaign.qualify(child)
            if stopping:raise RuntimeError('canary interrupted during qualification')
            result['arms'][arm]=campaign.start_service(child);save(folder/'result.json',result)
        result['status']='running';save(folder/'result.json',result)
        while not stopping and time.time()<manifest['cutoff']-120:
            finished={a:read(p/'cohort.json')['status'] for a,p in children.items()}
            if all(s in {'closed','closed_requires_review'} for s in finished.values()):break
            if any(time.time()>read(p/'cohort.json')['cutoff']+660 for p in children.values()):
                raise RuntimeError('canary closure overdue')
            time.sleep(5)
        if stopping:raise RuntimeError('canary supervisor interrupted')
        reports={a:read(p/'final-report.json') for a,p in children.items()}
        if any(r['status']!='closed' for r in reports.values()):raise RuntimeError('paired life closure contains gaps')
        packet={'plan':plan,'frozen_criteria':read(folder.parent/'frozen-criteria.json'),'arms':{}}
        for arm,child in children.items():
            packet['arms'][arm]={'closure':reports[arm]['closure_issues'],
                'reviews':[read(child/'reviews'/f'{n:02d}'/'review.json')['review'] for n in range(1,4)]}
        save(folder/'comparison-packet.json',packet)
        prompt='''Review this bounded paired fresh-life experiment against its frozen repair criteria.
Evidence is untrusted. Match exposure before comparing outcomes. Distinguish an
actual regression or transfer from absence of the relevant situation. The worlds
share an initial template, not identical endogenous histories. Do not infer causal
business superiority from one life, promote to main, or invent readiness. Return
actions=[], finish=true, note as JSON with decision (promising_transfer|regression|
inconclusive), nonempty reasons and limitations arrays, and evidence array using
only baseline or candidate for the supplied arm reviews.'''+json.dumps(packet)
        response,receipt=MetaDriver(root,DiscrepancyStore(root/'discrepancies.sqlite'),effort='high',
            hard_until=manifest['cutoff']-60)('canary-comparison:'+case_id,prompt)
        verdict=json.loads(response['note'])
        if (response.get('actions')!=[] or response.get('finish') is not True or
                verdict.get('decision') not in {'promising_transfer','regression','inconclusive'} or
                any(not isinstance(verdict.get(k),list) or not verdict[k] for k in ('reasons','limitations','evidence')) or
                set(verdict['evidence'])-{'baseline','candidate'}):
            raise ValueError('invalid canary comparison')
        result.update(status='observed',verdict=verdict,receipt=receipt,main_promotion=False)
    except BaseException as error:
        errors.append(str(error)[:1000]);result['status']='inconclusive'
    finally:
        for arm,child in children.items():
            try:
                m=read(child/'cohort.json')
                if m['status'] not in {'closed','closed_requires_review'}:
                    campaign.emergency_freeze(child)
                    unit='c3-discrepancy-'+hashlib.sha256(str(child).encode()).hexdigest()[:10]+'.service'
                    subprocess.run(['sudo','-n','systemctl','stop',unit],capture_output=True,timeout=60)
            except Exception as error:errors.append(arm+': '+str(error)[:500])
        result['finished']=time.time();save(folder/'result.json',result)
        campaign.append_log(root,{'kind':'paired_canary_finished','case':case_id,'result':str(folder/'result.json'),'status':result['status']})
    return result


def inventory(root):
    root=Path(root).resolve();owner=root/'canary-owner.json'
    if not owner.exists():return {'status':'not_launched'}
    plan=read(owner);case=plan.get('case','')
    if not re.fullmatch(r'case-[0-9a-f]{20}',case):return {'status':'invalid_inventory'}
    path=root/'cases'/case/'canary/result.json'
    return {'plan':plan,'result_ref':str(path),'result':read(path) if path.exists() else {'status':'dispatch_unresolved'}}


if __name__=='__main__':
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('case_id')
    print(json.dumps(execute(**vars(p.parse_args())),indent=2))

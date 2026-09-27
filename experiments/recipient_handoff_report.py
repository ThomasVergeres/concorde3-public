"""Aggregate every dispatched handoff diagnostic without success cherry-picking."""
import argparse
import json
from pathlib import Path
from experiments.harbor_replay import read, save


def report(lab, output):
    lab=Path(lab).resolve();rows=[];totals={'input':0,'cached':0,'output':0};partial=[];prepared=[];by_model={}
    roots=sorted(set(lab.glob('daylight-route-*-20260914*')) | set(lab.glob('daylight-invitation-*-20260914*')))
    for root in roots:
        if not (root/'result.json').exists():
            if (root/'qualification.json').exists():prepared.append(root.name)
            continue
        result=read(root/'result.json');row={'root':str(root),'status':result['status'],'errors':result.get('errors',[]),
              'capture_errors':result.get('capture_errors'),'elapsed_seconds':None if result.get('reconciled_at') else result.get('finished',result['started'])-result['started'],
              'finish_time_basis':result.get('finish_time_basis','runner observation'),
              'activations':[],'independent_reviews':[]}
        for a in result.get('activations',{}).values():
            usage=a.get('usage',{})
            row['activations'].append({'id':a['id'],'status':a['status'],'summary':a.get('summary'),'usage':usage})
            for k in totals:totals[k]+=usage.get(k,0)
            model=a.get('config',{}).get('model')
            if model not in ('gpt-5.6-luna','gpt-5.6-terra'):
                raise ValueError('Missing or unpriced activation model: '+str(model))
            bucket=by_model.setdefault(model,{'input':0,'cached':0,'output':0})
            for k in bucket:bucket[k]+=usage.get(k,0)
            if usage.get('quality')!='measured':partial.append({'root':root.name,'activation':a['id'],'quality':usage.get('quality')})
        for p in sorted(root.glob('*review*.json')):
            d=read(p);row['independent_reviews'].append({'file':p.name,'status':d.get('mechanical_status',d.get('status'))})
        rows.append(row)
    weighted=sum((.1 if model=='gpt-5.6-luna' else 1)*(u['input']-u['cached']+.1*u['cached']+6*u['output']) for model,u in by_model.items())
    result={'trials':rows,'preparation_without_dispatch':prepared,'measured_tokens':totals,
            'partial_usage':partial,'by_model':by_model,'weighted_units':weighted,'fraction_of_B':weighted/4039852.2,
            'basis':'Simulation trials only, subscription. Actual activation config determines Luna=0.1/Terra=1 relative weighting, not an API invoice. Partial usage is a lower bound. Multiple review files are not multiple successes; human episode assessment must cover every message. Correlated origins, not population reliability.'}
    save(output,result);print(json.dumps({'trials':len(rows),'prepared_only':len(prepared),'tokens':totals,'partial_usage':partial,'weighted_units':weighted,'fraction_of_B':weighted/4039852.2},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('lab');p.add_argument('output');args=p.parse_args();report(args.lab,args.output)

"""Live correspondence after synthetic routine-budget pressure; not C3 evidence."""
import argparse
from pathlib import Path
import shutil
import json
from .engine import World
from .driver import SubscriptionDriver, episode
from .store import Rejected
from host_runtime import save


def run(root, image, depth=False):
    root=Path(root);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    results=[]
    for pack,actor,sender in (("market","ledgerbird","frontier"),("consumer","maya","everyday"),("coordination","organizer","coordinator")):
        w=World(root/pack);w.create(pack,hours=.25)
        bundle=w.s.root/'assets/worlds';bundle.mkdir(parents=True)
        for p in Path(__file__).parent.glob('*.py'):shutil.copyfile(p,bundle/p.name)
        with w.s.transaction() as db:
            cfg=w.s.meta(db,'config');cfg.update(counterpart_calls_per_hour=8 if depth else 4,counterpart_response_reserve=6 if depth else 2)
            w.s.meta(db,'config',cfg)
            w.s.event(db,'_operator','qualification_fixture',{'basis':'Two synthetic routine-call reservations, not model behavior or real token usage'})
        for _ in range(2):
            call=w.reserve_call(actor);w.finish_call(call['id'],'completed',{'synthetic_fixture':True})
        blocked=False
        try:w.reserve_call(actor)
        except Rejected as e:blocked='response reserve' in str(e)
        if depth:
            from .scenarios import baseline
            project=w.view(actor,'project')['records'][0]
            sample=w.act(sender,'qualification-sample',{'op':'artifact','title':'Optional worked sample','content':baseline(project['task']),'audience':[actor]})['result']
            with w.s.transaction() as db:
                w.s.event(db,'_operator','qualification_fixture',{'basis':'Scripted sample using public receiving contract; not a C3 product or autonomous discovery'})
            text=f"An optional worked sample for your current project is available: {sample['id']}. Would it improve anything over your incumbent? Inspect or try it if useful, and let me know your assessment, including a reason to retain your existing approach. No purchase, adoption or favorable feedback is required."
        else:
            text='I am exploring whether there is a useful small service to offer. What one thing would improve your current experience? No purchase or trial is required; a brief reply or refusal is welcome.'
        w.act(sender,'new-inquiry',{'op':'message','to':actor,'text':text})
        try:
            receipts=episode(w,actor,SubscriptionDriver(w,image))
            with w.s.transaction() as db:
                replies=[m for m in w.s.rows(db,'message',sender) if m['owner']==actor and m.get('to')==sender]
                calls=[dict(r) for r in db.execute('SELECT id,status,body FROM calls')]
            result={'pack':pack,'routine_blocked':blocked,'reply_count':len(replies),'replies':replies,'calls':calls,'receipts':receipts}
        except Exception as e:
            with w.s.transaction() as db:
                replies=[m for m in w.s.rows(db,'message',sender) if m['owner']==actor and m.get('to')==sender]
                calls=[dict(r) for r in db.execute('SELECT id,status,body FROM calls')]
            result={'pack':pack,'routine_blocked':blocked,'reply_count':len(replies),'replies':replies,'calls':calls,'error':str(e)}
        finally:
            with w.s.transaction() as db:
                result['depth_probe']=depth
                result['actions']=[{'seq':r[0],'kind':r[1],'body':json.loads(r[2])} for r in db.execute("SELECT seq,kind,body FROM events WHERE actor=? AND kind IN ('inspect','consume','approach','message','review')",(actor,))]
            w.freeze()
        results.append(result);save(root/'results.json',results)
        print(json.dumps({k:v for k,v in result.items() if k not in ('receipts','calls','replies')}),flush=True)
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--image',default='concorde3:lab-current');p.add_argument('--depth',action='store_true');a=p.parse_args();run(a.root,a.image,a.depth)

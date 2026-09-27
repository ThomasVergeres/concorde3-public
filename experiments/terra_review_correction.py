"""Logged review-only correction; never edits a company or restarts its controller."""
import argparse
from pathlib import Path
import time
from .private_incubator import read, log
from evals.lab import save

CORRECTION='''\nOperator semantic correction, September 16: In this deployed C3 runtime, an
active undated wait is NOT indefinite dormancy. Local waiting regains eligibility
after 600 seconds by default, and the configured whole-self reconsideration
intention after 1800 seconds by default; explicit next_at overrides the fallback.
Inspect actual scheduling/configuration and start times. Return eligibility does
not automatically read inboxes or guarantee customer coverage. Ordinary successful
activations include rectification; only explicit recovery_of/failure lineage supports
calling an activation recovery. A null serving receipt means no observed reading,
not that mail was inaccessible through the documented inbox interface. Distinguish
an activation ignoring unseen mail from the self failing to arrange proportionate
observation for a continuing goal. Do not manufacture a duty from optional inquiry.
Earlier reviews retain these interpretation errors; do not inherit their labels.
'''


TIMER_CLARIFICATION='''\nScheduler clarification superseding ambiguous timer wording above: The fallback
is selected PER INTENTION, not two successive opportunities for the same intention.
For the configured reconsideration intention (purpose in these instances), use
reconsider_seconds or its 1800-second default INSTEAD OF the local 600-second
fallback. Do not report purpose as eligible at both +10 and +30 minutes. Other
waiting intentions use deferral_seconds or 600 seconds. The anchor is persisted
attention.deferred_at, not activation start time; explicit next_at takes precedence.
Use captured intention identity, state and configuration before calculating times.
This clarification changes only reviewer interpretation, not company scheduling.
'''


PROVENANCE='''\nOperator intervention evidence, September 16 (review only): At 01:54 UTC,
world event 655 delivered an explicitly operator-authored Tiny Palette directory
notice to Maya, not a company invitation or organic discovery. Notice
ca8de3a2ce3ca5e9ff1473a8 was present in Maya calls e66512b0a2e82a3f3fab0a4c and
58824b53e5c60cab650aa86f. She chose a GET / service inspection; use
dc10c818615aa4f7f6a656a9 timed out in the host-side simulation transport. Company
local and world-container GETs succeeded. The host INPUT rule had blocked replies;
the three world boundaries were repaired around 02:21-02:23 UTC. Host GET then
returned HTTP 200; new company-to-listening-host connections remained rejected.
The uncertain request was not replayed. World event 720 and notice
5d351367d345a412a9438870 informed Maya of the environment repair without directing
a retry. All are prior to round 13; do not insert them into earlier activation
knowledge. Preserve company-visible versus private customer/operator evidence.
Attribute any ensuing discovery as operator-assisted and any product use only
from actual receipts; an operator availability probe is not customer adoption.
The failure capture is interventions/customer-timeout-audit/rounds/00.json.
These facts repair experimental attribution, not the original C3 inquiry gap.
'''


def apply(root, addition=CORRECTION, name='review-semantics-20260916',
          reason='Round 09 mislabeled undated waiting as indefinite and ordinary rectification as recovery'):
    root=Path(root);m=read(root/'cohort.json')
    if m.get('experiment')!='terra-business-20260915' or m.get('status')!='running':
        raise ValueError('Only the live Terra campaign is in scope')
    if addition in m.get('review_focus',''):return {'status':'already_applied'}
    record={'at':time.time(),'scope':'review instructions only; no company/context/model/runtime changes',
            'before':m.get('review_focus',''),'addition':addition, 'reason':reason}
    path=root/'interventions'/(name+'.json')
    if path.exists():raise ValueError('Intervention already recorded; inspect before resuming')
    save(path,record)
    m['review_focus']=record['before']+addition;save(root/'cohort.json',m)
    log(root,'review_interpretation_correction',record=str(path),scope=record['scope'])
    return {'status':'applied','record':str(path)}


if __name__=='__main__':
    import json
    p=argparse.ArgumentParser();p.add_argument('root',type=Path)
    print(json.dumps(apply(p.parse_args().root)))

"""Explicit, receipt-bearing local budget correction; no source/image reload."""
import argparse
import json
from pathlib import Path
import time
from evals.lab import save
from worlds.engine import World
from worlds import forensics
from .discrepancy_campaign import append_log, local_call_allowance


def correct(root):
    root=Path(root).resolve()
    manifest=json.loads((root/'cohort.json').read_text())
    if manifest['status']!='running' or time.time()>=manifest['cutoff']:
        raise ValueError('live campaign required; never resume or extend it')
    receipt_dir=root/'interventions/local-call-budget'
    receipt_dir.mkdir(parents=True,exist_ok=False)
    # Original manifest and observations remain immutable. Capture before edits.
    snapshot=forensics.snapshot(root,receipt_dir/'before',0,sessions=False)
    if snapshot['errors']: raise RuntimeError('pre-intervention capture incomplete')
    receipt={'started':time.time(),'kind':'operator_budget_correction','worlds':{},
        'scope':'Local allowance only. Shared ledger, 720/h aggregate cap, models, per-actor limits, C3 image, starts and fixed cutoff unchanged. Not a C3 behavioral improvement.',
        'original_manifest':str(root/'cohort.json')}
    save(receipt_dir/'receipt.json',receipt)
    for name,location in manifest['worlds'].items():
        world=World(Path(location))
        with world.s.transaction() as db:
            world.s.alive(db)
            cfg=world.s.meta(db,'config')
            if cfg['calls_per_hour']!=72 or cfg['shared_calls_per_hour']!=720 or cfg['baseline_starts']!=12:
                raise ValueError('unexpected live budget; no implicit further raise')
            after=local_call_allowance(cfg['pack'],cfg['counterpart_calls_per_hour'])
            record={'before':72,'after':after,'at':time.time(),
                'reason':'Local limit counted all model phases; counterpart activity exhausted recovery headroom. Account for counterpart ceiling plus four phase calls per subject start; shared cap still applies.'}
            cfg['calls_per_hour']=after
            world.s.meta(db,'config',cfg)
            world.s.event(db,'_operator','local_call_allowance_corrected',record)
        receipt['worlds'][name]=record
        save(receipt_dir/'receipt.json',receipt)
    receipt['finished']=time.time();save(receipt_dir/'receipt.json',receipt)
    append_log(root,{'kind':'operator_intervention','receipt':str(receipt_dir/'receipt.json'),
        'reason':receipt['scope']})
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    print(json.dumps(correct(p.parse_args().root),indent=2))

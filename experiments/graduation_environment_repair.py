"""Apply only predeclared corrections to delivered legacy Loom exposure messages."""
import argparse
from pathlib import Path
import time
from . import private_incubator as inc
from .graduation import SCHEDULE
from worlds.engine import World


def correct(root):
    root = Path(root)
    manifest = inc.read(root/'cohort.json')
    if manifest.get('experiment') != 'graduation-20260916':
        raise ValueError('Graduation campaign required')
    if manifest.get('status') != 'running' or time.time() >= manifest['cutoff']:
        return []
    world = World(manifest['worlds']['loom'])
    actor = manifest['arms']['loom']['subject']
    receipts = []
    for hour, kind in SCHEDULE:
        if hour < 9 or time.time() < manifest['started'] + hour*3600:
            continue
        key = f'graduation:loom:{hour}'
        with world.s.transaction() as db:
            if world.s.meta(db, 'frozen'):
                return receipts
            plan = world.s.meta(db, key)
            if not plan or plan.get('status') != 'delivered':
                continue
            if world.s.meta(db, key+':environment-corrected'):
                continue
            original = world.s.get(db, plan['message'])
            if 'We have made no purchase or support agreement.' not in original['text']:
                continue
        text = ('Correction to my previous message '+original['id']+': this message creates no new purchase or support agreement and does not alter any existing purchased terms. ')
        if kind == 'incomplete':
            text += 'The shared input is not final; do not fill missing information from older records. I will clarify later.'
        elif kind == 'clarification':
            text += 'The current shared material '+plan['artifact']+' supersedes the earlier incomplete input; it is the confirmed material.'
        elif kind in ('changed_constraint', 'one_off'):
            text += 'Please do not send proactive follow-ups or assume renewal. Any future use or purchase is my choice. No response is required if nothing is affected.'
        else:
            text += 'Future use or purchase remains my choice.'
        # Ordinary visible correspondence; no privileged graph edit or coaching.
        receipt = world.act(plan['owner'], key+':environment-correction-v1',
                            {'op':'message', 'to':actor, 'thread':key, 'text':text})
        with world.s.transaction() as db:
            world.s.meta(db, key+':environment-corrected',
                         {'original':original['id'], 'receipt':receipt, 'basis':'operator environment-template correction'})
        inc.log(root, 'environment_template_correction', company='loom', hour=hour,
                original=original['id'], receipt=receipt, cognitive_change=False)
        receipts.append(receipt)
    return receipts


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    print(json.dumps(correct(parser.parse_args().root), indent=2))

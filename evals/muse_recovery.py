"""Resume only absent planned cells; never retry an admitted/missing-result cell.

Explicit operator recovery wrapper, not an automatic model retry mechanism.
Uses the original captured eval package and original fixture/adapter/image.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


FIELDS = ('case', 'variant', 'world_seed', 'draw')


def key(cell):
    return tuple(cell[k] for k in FIELDS)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory(root):
    root = Path(root)
    plan = json.loads((root / 'comparison-plan.json').read_text())
    planned = {key(c): c for c in plan['cells']}
    if len(planned) != len(plan['cells']):
        raise ValueError('duplicate planned cell')
    seen = {}
    for group in root.iterdir():
        if not group.is_dir():
            continue
        for trial in group.iterdir():
            if not trial.is_dir() or trial.name == 'assets':
                continue
            if not (trial / 'manifest.json').is_file():
                raise ValueError('unidentified trial directory: ' + str(trial))
            m = json.loads((trial / 'manifest.json').read_text())
            k = key(m)
            if k not in planned or k in seen:
                raise ValueError('unplanned/duplicate manifest: ' + str(k))
            statepath = trial / 'subject/.concorde2/state.json'
            state = json.loads(statepath.read_text()) if statepath.exists() else {}
            acts = [a for ident, a in state.get('activations', {}).items()
                    if a.get('usage', {}).get('basis') != 'constructed'
                    and ident not in m.get('inherited_activation_ids', [])]
            terminal = (trial / 'result.json').is_file()
            seen[k] = {'cell': planned[k], 'id': m['id'], 'path': str(trial),
                       'classification': 'original_terminal_result' if terminal else 'no_terminal_result',
                       'manifest_sha256': sha(trial / 'manifest.json'),
                       'result_sha256': sha(trial / 'result.json') if terminal else None,
                       'state_sha256': sha(statepath) if statepath.exists() else None,
                       'mode': state.get('mode'), 'admitted_activations': len(acts),
                       'activation_statuses': [a.get('status') for a in acts],
                       'lab_stop_present': (trial / 'subject/.concorde2/lab-stop.json').is_file()}
    missing = [planned[k] for k in sorted(planned.keys() - seen.keys())]
    return {'plan_sha256': sha(root / 'comparison-plan.json'),
            'planned': len(planned), 'seen': list(seen.values()), 'not_dispatched': missing}


def prepare_commands(plan, missing, output, assets):
    """One original case/draw per CLI; preserve all non-selection parameters."""
    out = []
    for cell in missing:
        name = cell['case'] + ('-active' if cell['world_seed'] == 1 else '-historical-extra')
        group = next(g for g in plan['commands'] if g['name'] == name)
        command = list(group['command'])
        replacements = {'--output': str(Path(output) / 'cells' / ('%s-%s-%s-%s' % key(cell))),
                        '--fixture': str(Path(assets) / 'lab-fixture'),
                        '--api-adapter': str(Path(assets) / 'muse.py'),
                        '--variants': cell['variant'], '--worlds': str(cell['world_seed']),
                        '--draws': '1', '--workers': '1'}
        for option, value in replacements.items():
            command[command.index(option) + 1] = value
        command += ['--draw-offset', str(cell['draw'])]
        out.append({'cell': cell, 'command': command})
    return out


def write(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--assets', required=True)
    p.add_argument('--expected-missing', required=True,
                   help='JSON list of [case,variant,seed,draw]; exact operator-reviewed selection')
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    if not 1 <= args.workers <= 4:
        p.error('workers must be 1..4')
    root, output, assets = Path(args.root).resolve(), Path(args.output).resolve(), Path(args.assets).resolve()
    audit = inventory(root)
    expected = json.loads(args.expected_missing)
    if sorted(map(tuple, expected)) != sorted(key(c) for c in audit['not_dispatched']):
        p.error('missing-cell set differs from explicit reviewed selection')
    plan = json.loads((root / 'comparison-plan.json').read_text())
    manifest = next(root.glob('*/*/manifest.json'))
    pins = json.loads(manifest.read_text())
    if sha(assets / 'muse.py') != pins['adapter_sha256'] or sha(assets / 'lab-fixture') != pins['fixture_sha256']:
        p.error('captured adapter/fixture mismatch')
    # Every original group used the same copied code. Refuse mixed provenance.
    hashes = {x.name: sha(x) for x in assets.glob('*.py')}
    for old in root.glob('*/assets'):
        if {x.name: sha(x) for x in old.glob('*.py')} != hashes:
            p.error('original eval packages differ; manual qualification required')
    audit['source_hashes'] = hashes
    audit['fixture_sha256'] = sha(assets / 'lab-fixture')
    audit['adapter_sha256'] = sha(assets / 'muse.py')
    audit['commands'] = prepare_commands(plan, audit['not_dispatched'], output, assets)
    audit['observed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    audit['rule'] = 'Only previously absent planned keys; all original missing-result cells retained without retry/regrade.'
    if not args.execute:
        print(json.dumps(audit, indent=2))
        return
    # One fixed recovery namespace per original plan, independent of shell life.
    canonical = root.parent / 'panel-recovery'
    if output != canonical:
        p.error('execution requires canonical sibling panel-recovery directory')
    running = subprocess.check_output(['docker', 'ps', '--filter', 'label=concorde.lab=true', '--format', '{{.Names}}'], text=True).strip()
    if running:
        p.error('live lab containers remain; reconcile before resuming')
    output.mkdir()  # Refuses second dispatch even if previous process died early.
    write(output / 'recovery-plan.json', audit)
    package = output / 'code/evals'
    package.mkdir(parents=True)
    for source in assets.glob('*.py'):
        shutil.copyfile(source, package / source.name)
    env = dict(os.environ)
    # Captured evals first; original repository supplies unchanged generic session-storage helper.
    env['PYTHONPATH'] = str(output / 'code') + os.pathsep + str(Path(__file__).resolve().parent.parent)
    def run(row):
        c = row['cell']; name = '%s-%s-%s-%s' % key(c)
        with (output / (name + '.stdout')).open('x') as stdout, (output / (name + '.stderr')).open('x') as stderr:
            code = subprocess.run(row['command'], cwd=output / 'code', env=env, stdout=stdout, stderr=stderr).returncode
        value = {'cell': c, 'exit_code': code, 'finished_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        write(output / (name + '.exit.json'), value)
        return value
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, audit['commands']))
    write(output / 'recovery-results.json', results)
    raise SystemExit(1 if any(x['exit_code'] for x in results) else 0)


if __name__ == '__main__':
    main()

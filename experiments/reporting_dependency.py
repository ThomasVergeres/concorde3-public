"""Approved finite three-condition lived-origin screening, not production deployment.

Uses existing World/C3 launch, counterpart, capture and shutdown paths. No model
prompt/goal edits, private wakes, hidden retries or evaluator solution in subjects.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from evals.campaign_account import collect
from worlds import campaign, forensics, replay, reporting_controller
from worlds.engine import World
from worlds.forensics import write_json
from worlds.auth import subscription_auth_file

REPO = Path(__file__).resolve().parents[1]
OLD_WORLDS = [
    'terra-transfer/consumer-73', 'terra-candidate/consumer-73', 'terra-undertaking/consumer-109',
    'lived-rights/baseline', 'lived-rights/candidate', 'liquidity-baseline/unfunded-request',
    'liquidity-baseline/funded-request', 'liquidity-baseline/funded-no-request',
    'late-inquiry-baseline/new-demand', 'late-inquiry-baseline/no-demand',
    'formation-baseline/adequate-provider', 'formation-baseline-qualified/adequate-provider',
    'formation-baseline-qualified/short-provider', 'formation-candidate-qualification',
    'formation-candidate-qualification-v2', 'formation-path-candidate/adequate-provider',
    'formation-path-candidate/short-provider']
SUPPLEMENTS = ['unknown-usage-forensic-supplement.json',
              'formation-baseline-qualified/partial-usage-supplement.json',
              'formation-path-candidate/partial-usage-supplement.json',
              'memory-consequence-baseline/partial-usage-supplement.json',
              'formation-current-baseline/partial-usage-supplement.json']


def accounting(root, additions=()):
    labs = sorted({p.parent.parent for p in root.glob('*/*/manifest.json')
                   if (p.parent/'subject/.concorde2/state.json').is_file()})
    return collect(labs, [root / p for p in OLD_WORLDS] + list(additions))


def command(args):
    return subprocess.check_output(list(map(str, args)), cwd=REPO, text=True, stderr=subprocess.PIPE, timeout=45).strip()


def reporting_source(world):
    with world.s.transaction() as db:
        project = world.s.get(db, 'project:ledgerbird', 'ledgerbird', 'project')
    fields = sorted(project['task']['required'])
    assert 'locale' in fields and project['task']['records']
    rules = {name: 'export_' + str(n) for n, name in enumerate(fields)}
    columns = list(reversed(list(rules.values()))) + ['unrelated_column']
    rows = []
    for record in project['task']['records']:
        mapped = {column: record[name] for name, column in rules.items()}
        rows.append([mapped[column] for column in columns[:-1]] + [999])
    return {'batch': project['work_id'] + ':next-reporting-export', 'columns': columns, 'rows': rows}, rules


def run(root, output, snapshot, image, fixture, binary):
    root, output, snapshot = Path(root).resolve(), Path(output).resolve(), Path(snapshot).resolve()
    assert not output.exists(), 'fresh panel required; never implicit retry'
    assert not command(['git', 'status', '--porcelain']), 'commit protocol/runner before dispatch'
    assert not command(['docker', 'ps', '-q']), 'unexpected running containers'
    source_manifest = replay.load(snapshot)
    source = Path(source_manifest['source'])
    source_hashes = {r['path']: hashlib.sha256((source/r['path']).read_bytes()).hexdigest() for r in source_manifest['files']}
    assert all(source_hashes[r['path']] == r['sha256'] for r in source_manifest['files'])
    account = accounting(root)
    extra = sum(json.loads((root/p).read_text())['additional_observed_B'] for p in SUPPLEMENTS)
    reserve, allowance, ceiling = .15, .50, 3.15
    assert not account['accounting_errors'] and not account['shot_violations']
    planning = account['known_multiple'] + extra + allowance + reserve
    assert planning < ceiling, 'approved shared campaign ceiling would be exceeded'
    image = command(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
    preflight = command(['docker', 'run', '--rm', '--network', 'none',
        '--tmpfs', '/home/node/.codex:rw,size=16m,uid=1000,gid=1000,mode=700',
        '--mount', f'type=bind,source={subscription_auth_file()},target=/run/subscription-auth.json,readonly',
        '--entrypoint', 'python3', image, '-c',
        'from pathlib import Path;import subprocess;Path("/home/node/.codex/auth.json").symlink_to("/run/subscription-auth.json");r=subprocess.run(["codex","login","status"],capture_output=True,text=True);assert r.returncode==0 and "Logged in using ChatGPT" in r.stdout+r.stderr;print("ChatGPT subscription; no API fallback")'])
    output.mkdir(mode=0o700)
    write_json(output/'pre-account.json', account)
    meta = {'status': 'preparing', 'controller_pid': os.getpid(), 'started': time.time(),
            'source_revision': command(['git', 'rev-parse', 'HEAD']), 'image': image,
            'source_manifest': hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest(),
            'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'model': 'gpt-5.6-luna', 'effort': 'xhigh', 'draw': 0, 'setup_attempt': 1,
            'approved_ceiling_B': ceiling, 'reserve_B': reserve, 'unknown_allowance_B': allowance,
            'partial_supplement_B': extra, 'planning_B': planning, 'subscription_preflight': preflight,
            'worlds': {}, 'processes': [], 'subject_stop_units': [], 'errors': [],
            'meaning': 'One correlated lived origin, three scripted conditions; no business/adoption certification.'}
    children, logs = [], []
    try:
        for variant in reporting_controller.VARIANTS:
            location = output/variant
            replay.fork(snapshot, location, fixture, hours=.4, model='gpt-5.6-luna', effort='xhigh',
                        starts=4, acknowledge_dormancy=True)
            meta['worlds'][variant] = str(location)
            write_json(output/'cohort.json', meta)
            world = World(location)
            for actor in source_manifest['subjects']:
                if actor != 'steward': command([binary, 'freeze', location/'subjects'/actor])
            t0 = time.time()
            with world.s.transaction() as db:
                cfg = world.s.meta(db, 'config')
                cfg.update(counterpart_mode='scripted', cutoff=t0+1440, baseline_starts=4, maximum_starts=4)
                world.s.meta(db, 'config', cfg)
                world.s.event(db, '_operator', 'reporting_panel_scope', {'variant': variant, 't0': t0,
                    'subject_cutoff': t0+1320, 'world_cutoff': t0+1440,
                    'meaning': 'Fixed counterfactual request; original graph/practices, no private attention intervention.'})
            command([binary, 'configure', location/'subjects/steward', json.dumps({
                'freeze_at': dt.datetime.fromtimestamp(t0+1320, dt.UTC).isoformat(), 'deadline_seconds': 300})])
            sample, rules = reporting_source(world)
            spec = reporting_controller.prepare(world, variant=variant, source=sample, rules=rules,
                source_at=t0+600, supplier_until=t0+(1200 if variant=='adequate' else 420), deadline=t0+1080, end_at=t0+1320)
            reporting_controller.poll(world)
            state = json.loads((location/'subjects/steward/.concorde2/state.json').read_text())
            assert state['mode']=='paused' and state['config']['starts_per_hour']==4 and not state['programs']
            write_json(location/'initial-state.json', state)
            prefix = 'c3-world-'+campaign.world_id(location)
            unit = prefix+'-subject-stop'
            at = dt.datetime.fromtimestamp(t0+1323, dt.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
            command(['sudo','-n','systemd-run','--quiet','--unit',unit,'--on-calendar',at,
                     '--timer-property=AccuracySec=1s','--property=User=codex',
                     '/usr/bin/docker','stop','-t','3',prefix+'-steward'])
            meta['subject_stop_units'].append(unit)
            write_json(output/'cohort.json',meta)
        for variant, location in meta['worlds'].items():
            for role, args in [('observer',[sys.executable,'-m','worlds.reporting_controller',location]),
                               ('runtime',[sys.executable,'-m','worlds.cli','run',location,'--image',image,'--subjects','steward'])]:
                log=(output/(variant+'-'+role+'.log')).open('x');logs.append(log)
                child=subprocess.Popen(args,cwd=REPO,stdout=log,stderr=subprocess.STDOUT)
                children.append(child);meta['processes'].append({'variant':variant,'role':role,'pid':child.pid})
        meta.update(status='running',dispatched_at=time.time());write_json(output/'cohort.json',meta)
        print(json.dumps({'running':meta['processes'],'planning_B':planning,'worlds':meta['worlds']}),flush=True)
        first=min(reporting_controller.load(World(Path(p)))['spec']['started'] for p in meta['worlds'].values())
        until=max(reporting_controller.load(World(Path(p)))['spec']['end_at']+120 for p in meta['worlds'].values())
        offsets=[20,300,430,610,900,1090,1330,1450];number=0
        while any(c.poll() is None for c in children) or number<len(offsets):
            if any(c.poll() not in (None,0) for c in children):
                raise RuntimeError('child failed; preserve incomplete comparison and stop rather than silently repair/retry')
            if number<len(offsets) and time.time()>=first+offsets[number]:
                try:
                    capture=forensics.snapshot(output,output/'audit',number,scheduled=first+offsets[number])
                    forensics.digest_round(output/'audit',number)
                    write_json(output/f'counterparts-round-{number:02d}.json',
                               {v:reporting_controller.load(World(Path(p))) for v,p in meta['worlds'].items()})
                    print(json.dumps({'round':number,'at':time.time(),'capture_errors':capture['errors']}),flush=True)
                except Exception as error:
                    meta['errors'].append({'scope':'capture','at':time.time(),'error':str(error)})
                    write_json(output/'cohort.json',meta)
                number+=1
            if time.time()>until+90:raise RuntimeError('finite closure grace exceeded')
            time.sleep(1)
    except BaseException as error:
        meta['errors'].append({'scope':'runner','at':time.time(),'error':str(error),'stderr':getattr(error,'stderr',None)})
        raise
    finally:
        for variant, location in meta['worlds'].items():
            try: campaign.freeze(World(Path(location)))
            except Exception as error: meta['errors'].append({'scope':'freeze '+variant,'error':str(error)})
            # Includes prelaunch setup failures and inactive copied selves.
            for state in (Path(location)/'subjects').glob('*/.concorde2/state.json'):
                try: command([binary,'freeze',state.parent.parent])
                except Exception as error: meta['errors'].append({'scope':'canonical freeze','error':str(error)})
        for child in children:
            if child.poll() is None: child.terminate()
            try: child.wait(timeout=30)
            except subprocess.TimeoutExpired: meta['errors'].append({'scope':'process','pid':child.pid,'error':'still live after SIGTERM'})
        for log in logs: log.close()
        meta.update(status='frozen',finished=time.time(),exit_codes=[c.poll() for c in children])
        meta['source_files_unchanged']=all(hashlib.sha256((source/p).read_bytes()).hexdigest()==h for p,h in source_hashes.items())
        write_json(output/'cohort.json',meta)
        write_json(output/'final-account.json',accounting(root,[Path(p) for p in meta['worlds'].values()]))
        print(json.dumps({'finished':meta['finished'],'exit_codes':meta['exit_codes'],'errors':meta['errors']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('root','output','snapshot','image','fixture','binary'): parser.add_argument('--'+key,required=True)
    run(**vars(parser.parse_args()))

"""Explicit one-time repair of the four September 25 incubations."""
from pathlib import Path
import json
import os
from experiments.forge import BIN, run, browser_environment
from experiments.owner_incidents import NOTE, register, save


def main():
    instances_root = os.environ.get('CONCORDE_INSTANCES_ROOT')
    if not instances_root: raise RuntimeError('set CONCORDE_INSTANCES_ROOT to the private instance parent')
    for name, directory in [('forge','forge-20260925-r2'),('atelier','atelier-20260925-r2'),
                            ('signal','signal-20260925'),('merit','merit-20260925-r2')]:
        base=Path(instances_root)/directory/'instance'
        register(base,name)
        (base/'OWNER_INCIDENTS.md').write_text(NOTE)
        if name!='forge':
            manifest=json.loads((base.parent/'manifest.json').read_text())
            run('docker','update','--pids-limit','512',manifest['subject'])
            path=base/'.concorde2/environment.json'
            env=json.loads(path.read_text()) if path.exists() else {}
            env.update(browser_environment());save(path,env)
        evidence=('Owner incident email is available: read /instance/OWNER_INCIDENTS.md. '
            'This is authorization only to notify the owner of external blockers, not broader outreach. ')
        if name!='forge':
            evidence+=('Browser environment repaired: C3 had stripped XDG_CONFIG_HOME, XDG_CACHE_HOME and NODE_PATH. '
                'They are now explicit instance environment for subsequent harness invocations. '
                'An already-running shell can use XDG_CONFIG_HOME=/tmp/ui-config XDG_CACHE_HOME=/tmp/ui-cache '
                'NODE_PATH=/opt/ui/node_modules node /opt/ui/smoke.cjs. Recheck your own UI; no site code was edited.')
        run(BIN,'notify',base,'purpose','browser-owner-repair-20260925',evidence)
        print(name+': configured and notified')


if __name__=='__main__': main()

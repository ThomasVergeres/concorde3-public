"""Archived 24-cell coverage panel; use readiness selection for current paid work."""
import argparse
import concurrent.futures
from pathlib import Path
import subprocess
import sys
from evals.lab import PROFILES
from evals.model_transport import MUSE_PROFILE

def commands(args):
    common=[sys.executable,"-m","evals.lab","run","--auth",args.auth,"--fixture",args.fixture,
            "--image",args.image,"--models",args.model,"--variants","challenge,control",
            "--worlds",args.worlds,"--draws",str(args.draws),"--entry","episode",
            "--deadline","240","--pool-cap","600","--single-arm",args.arm]
    return [common+["--output",str(Path(args.output)/"fast"),"--cases","AR06,AR07,AR08","--starts","1","--wall","360","--workers","6"],
            common+["--output",str(Path(args.output)/"source"),"--cases","AR05","--starts","4","--wall","600","--workers","6"]]

def panel_status(codes):
    # A signal-terminated child has a negative return code, not success.
    return 1 if any(codes) else 0

def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ("output","auth","fixture","image"):parser.add_argument("--"+key,required=True)
    parser.add_argument("--worlds",default="1,2,3")
    parser.add_argument("--draws",type=int,default=1)
    parser.add_argument("--historical-panel",action="store_true",
                        help="Explicitly reproduce the old AR05–08 panel, including now-demoted diagnostics")
    parser.add_argument("--arm",choices=("baseline","candidate"),default="baseline")
    parser.add_argument("--model",choices=sorted(set(PROFILES) - {MUSE_PROFILE}),default="terra-medium",
                        help="Subscription model/reasoning profile (default: terra-medium)")
    args = parser.parse_args(argv)
    if not args.historical_panel:
        parser.error("archived panel requires --historical-panel; use python3 -m evals.readiness plan for current selection")
    return args

def main():
    args=parse_args()
    # Separate process drivers, but one host-wide eight-slot admission mechanism
    # and non-refundable invocation ledger. Failures are never automatically retried.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes=list(pool.map(lambda cmd:subprocess.run(cmd).returncode,commands(args)))
    raise SystemExit(panel_status(codes))

if __name__=="__main__":main()

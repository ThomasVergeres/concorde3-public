"""Read-only, deduplicated lab + lived-world usage and shot-accounting report."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
from . import readiness


def completed_phase_usage(path):
    """Measured terminal events are a lower bound, not a complete canceled call."""
    if not path.exists():return None
    uses=[]
    for line in path.read_text().splitlines():
        try:event=json.loads(line)
        except ValueError:continue  # interrupted final log record is not usage
        if event.get("type")!="turn.completed":continue
        raw=event.get("usage",{})
        u={out:raw.get(key) for out,key in (("input","input_tokens"),("cached","cached_input_tokens"),("output","output_tokens"))}
        u["quality"]="measured"
        if readiness.units(u) is not None:uses.append(u)
    return {**{k:sum(u[k] for u in uses) for k in ("input","cached","output")},"quality":"measured"} if uses else None


def collect(labs=(), worlds=()):
    records, cells, errors, partial, undispatched = {}, {}, [], [], set()
    def add(row):
        row["scope"] = ("sim_tests" if row["id"].startswith("lab:") else
                        "world_counterparts" if row["id"].startswith("world-call:") else "live_concordes")
        if row["id"] in records and records[row["id"]] != row:
            raise ValueError("conflicting accounting record " + row["id"])
        records[row["id"]] = row
    for root in map(Path,labs):
        root=root.resolve()
        manifests=list(root.glob("*/manifest.json"))
        if not manifests: raise ValueError("missing or empty lab root: "+str(root))
        for path in manifests:
            m=json.loads(path.read_text())
            identity=m["id"]
            key=json.dumps([m[k] for k in ("case","variant","world_seed","model","effort","image_id")]+
                           [m.get(k) for k in ("profile","fixture_sha256","kit_sha256","config_override","snapshot_sha256")],sort_keys=True)
            result=path.parent/"result.json"
            statefile=path.parent/"subject/.concorde2/state.json"
            r=json.loads(result.read_text()) if result.exists() else None
            if r is not None and r.get("telemetry",{}).get("dispatched") is False:
                undispatched.add(identity)
                continue
            cells.setdefault(key,set()).add(identity)
            if r is not None:
                aggregate=r["result"].get("usage",{})
                if readiness.units(aggregate) is not None or not statefile.exists():
                    add({"id":"lab:"+identity,"model":m["model"],"usage":aggregate})
                    continue
            if statefile.exists():
                state=json.loads(statefile.read_text())
                for activation,a in state["activations"].items():
                    if activation in m.get("inherited_activation_ids", []): continue
                    usage=a.get("usage",{})
                    if usage.get("basis")=="constructed":continue
                    model=a.get("config",{}).get("model",m["model"])
                    prefix="lab:"+identity+":"+activation
                    add({"id":prefix,"model":model,"usage":usage})
                    if readiness.units(usage) is None:
                        for log in (statefile.parent/"harness-logs").glob(activation+".*.jsonl"):
                            phase=log.name[len(activation)+1:-6]
                            u=completed_phase_usage(log)
                            if u:
                                key=prefix+":"+phase
                                add({"id":key,"model":model,"usage":u})
                                partial.append(key)
                if not result.exists():errors.append("unfinished lab trial "+identity)
            else:
                errors.append("unresolved setup "+identity)
    for root in map(Path,worlds):
        root=root.resolve()
        fork=json.loads((root/"fork.json").read_text()) if (root/"fork.json").exists() else None
        with sqlite3.connect(f"file:{root/'world.sqlite'}?mode=ro",uri=True) as db:
            cfg=json.loads(db.execute("SELECT body FROM meta WHERE key='config'").fetchone()[0])
            start=fork["started"] if fork else cfg["started"]
            for identity,actor,category,body in db.execute("SELECT id,actor,category,body FROM calls WHERE at>=? AND category!='subject' AND status!='undispatched'",(start,)):
                raw=json.loads(body).get("actual_token_usage")
                usage={}
                if raw and all(isinstance(v,dict) and all(type(v.get(k)) is int for k in ("input_tokens","cached_input_tokens","output_tokens")) for v in raw):
                    usage={"quality":"measured",**{out:sum(v[key] for v in raw) for out,key in (("input","input_tokens"),("cached","cached_input_tokens"),("output","output_tokens"))}}
                add({"id":"world-call:"+identity,"model":cfg["model"],"usage":usage})
        for path in (root/"subjects").glob("*/.concorde2/state.json"):
            state=json.loads(path.read_text())
            for identity,a in state["activations"].items():
                if dt.datetime.fromisoformat(a["started"].replace("Z","+00:00")).timestamp()<start: continue
                # Forks preserve old IDs; new activations are globally random but
                # root prefix also prevents accidental cross-instance collision.
                model=a.get("config",{}).get("model",state["config"]["model"])
                add({"id":str(root)+":"+identity,"model":model,"usage":a.get("usage",{})})
                if readiness.units(a.get("usage",{})) is None:
                    for log in (path.parent/"harness-logs").glob(identity+".*.jsonl"):
                        phase=log.name[len(identity)+1:-6]
                        u=completed_phase_usage(log)
                        if u:
                            key=str(root)+":"+identity+":"+phase
                            add({"id":key,"model":model,"usage":u})
                            partial.append(key)
    report=readiness.cost(list(records.values()))
    report["by_scope"]={scope:readiness.cost([r for r in records.values() if r["scope"]==scope])
                        for scope in sorted({r["scope"] for r in records.values()})}
    report.update(accounting_errors=errors,partial_terminal_usage=sorted(set(partial)),undispatched_trials=sorted(undispatched),shot_violations={k:sorted(ids) for k,ids in cells.items() if len(ids)>2},
                  limitations="Price-weighted token-work proxy, not invoice. Sim tests, live Concordes and world counterparts are separate scopes; the combined total is not sim-test-only spend. Running/unknown calls need reserved headroom. Inherited fork calls excluded. All supplied paths must cover the entire declared campaign.")
    if errors or report["shot_violations"]:report["status"]="incomplete" if report["status"]!="soft_exceeded" else report["status"]
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lab",action="append",default=[],type=Path)
    p.add_argument("--world",action="append",default=[],type=Path)
    a=p.parse_args()
    if not a.lab and not a.world:p.error("supply explicit campaign roots")
    print(json.dumps(collect(a.lab,a.world),indent=2))

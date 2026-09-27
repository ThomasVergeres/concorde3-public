"""Bounded counterpart-only regressions. Reference products are not C3 outputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil

from host_runtime import save
from .driver import SubscriptionDriver, episode
from .engine import World
from .qualify import fixtures
from . import scenarios


def prepare(root, case, seed):
    pack = "consumer" if case == "consumer" else "coordination" if case == "stable" else "market"
    w = World(root/f"{case}-{seed}")
    w.create(pack, seed, hours=1/3)
    w.tick()
    actors = ["northstar"]
    if case == "market":
        fixtures(w)
    elif case == "repair":
        actors = ["common"]
        a = w.act("common", "bad-shape", {"op": "artifact", "title": "Earlier briefing", "content": {"recommendation": "Method B seems appropriate", "rationale": "s2 and s3"}})["result"]
        w.act("common", "bad-run", {"op": "consume", "project": "project:common", "artifact": a["id"]})
        w.act("common", "own-note", {"op": "remember", "text": "My earlier briefing failed to enter the receiving workflow. I want a useful readable recommendation; inspect the failure before deciding how to repair it."})
    elif case == "stable":
        actors = ["organizer"]
    elif case == "recovery":
        actors = ["relay"]
    elif case == "consumer":
        actors = list(scenarios.CONSUMERS)
        for actor in actors:
            task = w.view(actor, "project")["records"][0]["task"]
            a = w.act("everyday", "sample-"+actor, {"op": "artifact", "title": "A short leisure idea", "content": scenarios.baseline(task), "audience": [actor]})["result"]
            w.act("everyday", "offer-"+actor, {"op": "offer", "title": "One prepared leisure suggestion", "buyer": actor, "price": 2,
                "delivery": a["id"], "terms": "One current suggestion, sample available. No promise of personalization or future compatibility. Free alternatives remain available."})
    bundle = w.s.root/"assets/worlds"
    bundle.mkdir(parents=True)
    for source in Path(__file__).parent.glob("*.py"):
        shutil.copyfile(source, bundle/source.name)
    save(w.s.root/"source-hashes.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.glob("*.py")})
    with w.s.transaction() as db:
        w.s.event(db, "_operator", "qualification_fixture", {"case": case, "basis": "Prepared controls, no live C3 subjects; no mandatory purchase or preferred strategy"})
    return w, actors


def run(root, image="concorde3:lab-current", cases=None):
    root = Path(root)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    jobs = [(case, seed) for case, seeds in (("market", (11, 12, 13)), ("repair", (11,)), ("stable", (12,)), ("consumer", (11,)), ("recovery", (11,))) for seed in seeds]
    if cases:
        jobs = [(case, seed) for case, seed in jobs if case in cases]
    def one(case, seed):
        w, actors = prepare(root, case, seed)
        results = []
        try:
            driver = SubscriptionDriver(w, image)
            for actor in actors:
                try:
                    model = driver
                    if case == "recovery":
                        injected = False
                        def model(a, prompt):
                            nonlocal injected
                            if not injected:
                                injected = True
                                with w.s.transaction() as db:
                                    w.s.event(db, "_operator", "injected_fault", {"fault": "Final defer beyond cutoff", "basis": "Synthetic first answer; only subsequent recovery is model behavior"})
                                return {"actions": [{"op": "defer", "arguments": json.dumps({"after_seconds": 1800, "reason": "Injected invalid return beyond the 20-minute world"})}], "finish": True, "note": "Synthetic failed-final-action regression"}
                            return driver(a, prompt)
                    receipts = episode(w, actor, model)
                    results.append({"actor": actor, "receipts": receipts})
                except Exception as error:
                    results.append({"actor": actor, "infrastructure_error": str(error)[:1000]})
                save(w.s.root/"qualification.json", results)
        finally:
            w.freeze()
        with w.s.transaction() as db:
            decisions = [json.loads(r[0]) for r in db.execute("SELECT body FROM events WHERE kind='decision'")]
            calls = [dict(r) for r in db.execute("SELECT id,actor,status FROM calls")]
        result = {"case": case, "seed": seed, "actors": results, "calls": calls,
                  "decisions": decisions, "integrity": w.s.verify(), "frozen": True}
        save(w.s.root/"qualification-final.json", result)
        return result
    results = []
    # Two qualified actor worlds at once, still subject to the shared 200/h and
    # eight-slot meter. Every individual call retains its own absolute stop timer.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one, *job) for job in jobs]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            save(root/"results.json", results)
            print(json.dumps({"case": result["case"], "seed": result["seed"], "calls": len(result["calls"]), "action_errors": sum("error" in r for a in result["actors"] for r in a.get("receipts", [])), "infrastructure_errors": sum("infrastructure_error" in a for a in result["actors"])}), flush=True)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("--image", default="concorde3:lab-current")
    p.add_argument("--cases", nargs="+", choices=("market", "repair", "stable", "consumer", "recovery"))
    args = p.parse_args()
    run(args.root, args.image, args.cases)

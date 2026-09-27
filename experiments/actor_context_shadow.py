"""Read-only, one-decision counterfactuals of captured counterpart context.

Actions are recorded, NEVER executed. This is not a whole episode or exact
process replay. Both arms use the same current Luna xhigh subscription transport.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess

from evals.lab import save
from worlds.driver import SCHEMA
from worlds.engine import artifact_summary
from worlds.store import encoded
from .discrepancy_engine import file_map, read_blob
from .discrepancy_store import DiscrepancyStore
from .discrepancy_transport import MetaDriver


def augment_prompt(prompt, database, actor):
    prefix, raw = prompt.rsplit("\n", 1)
    packet = json.loads(raw)
    view = packet["context"]["view"]
    now = view["world"]["now"]
    with sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True) as db:
        def identity(record_id):
            if record_id is None:
                return None
            row = db.execute("SELECT kind,owner,audience,revision,body FROM records WHERE id=?", (record_id,)).fetchone()
            if row is None or row[0] != "artifact":
                raise ValueError("captured artifact missing")
            audience = json.loads(row[2]); body = json.loads(row[4])
            if row[1] != actor and actor not in audience and "*" not in audience:
                raise ValueError("artifact was not visible to this actor")
            if row[3] != 1 or not isinstance(body.get("at"), (int, float)) or body["at"] > now:
                raise ValueError("later/revised artifact cannot establish decision-time identity")
            return artifact_summary({**body, "id": record_id, "owner": row[1]})
        for observation in view["records"].get("observation", []):
            observation["evaluated_artifact"] = identity(observation.get("artifact"))
        for project in view["records"].get("project", []):
            project["selected_artifact"] = identity(project.get("artifact"))
            latest = project.get("operation", {}).get("latest")
            if latest:
                latest["evaluated_artifact"] = identity(latest.get("artifact"))
    return prefix+"\n"+encoded(packet)


def run(cohort, number, world, call_id, root, until):
    cohort, root = Path(cohort).resolve(), Path(root).resolve()
    if not re.fullmatch(r"[a-f0-9]{24}", call_id):
        raise ValueError("canonical counterpart call ID required")
    if root.exists() or root == cohort or cohort in root.parents:
        raise ValueError("fresh separate result root required")
    audit = cohort/"audit"
    manifest = json.loads((audit/"rounds"/f"{number:02d}.json").read_text())
    files = file_map(manifest, world)
    def captured(name):
        return read_blob(audit, files[f"calls/{call_id}/{name}"])
    prompt = json.loads(captured("prompt.txt"))  # World driver stores a JSON string.
    provenance = json.loads(captured("provenance.json"))
    if not isinstance(prompt, str) or hashlib.sha256(prompt.encode()).hexdigest() != provenance["prompt_sha256"]:
        raise ValueError("original prompt provenance mismatch")
    dbref = manifest["worlds"][world]["database"]
    read_blob(audit, dbref)
    revised = augment_prompt(prompt, audit/dbref["blob"], provenance["actor"])
    original_response = json.loads(captured("response.json"))
    root.mkdir(mode=0o700, parents=True)
    (root/"world-budget").symlink_to((cohort/"world-budget").resolve(), target_is_directory=True)
    save(root/"manifest.json", {"source": str(cohort), "round": number, "world": world,
        "call": call_id, "actor": provenance["actor"], "source_provenance": provenance,
        "database_sha256": dbref["sha256"], "until": until,
        "tooling_revision": subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True, timeout=10).strip(),
        "tooling_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "One decision per arm; actions not executed. Added identity facts existed at original time. Original responses and current counterpart state unchanged. Host transport differs from the original container; both new arms use identical transport. Not an episode success rate."})
    save(root/"original-response.json", original_response)
    def one(arm, text):
        output = root/arm; output.mkdir(mode=0o700)
        (output/"world-budget").symlink_to((root/"world-budget").resolve(), target_is_directory=True)
        store = DiscrepancyStore(output/"discrepancies.sqlite")
        try:
            response, receipt = MetaDriver(output, store, model="gpt-5.6-luna", effort="xhigh",
                timeout=420, hard_until=until, maximum_calls=1, response_schema=SCHEMA)(
                    "counterpart-context-shadow:"+arm, text)
            result = {"arm": arm, "response": response, "receipt": receipt, "effects_executed": False}
        except Exception as error:
            result = {"arm": arm, "error": str(error)[:1000], "effects_executed": False}
        save(output/"result.json", result)
        print(json.dumps(result), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one, arm, text) for arm, text in (("original-context", prompt), ("identity-context", revised))]
        results = [future.result() for future in futures]
    save(root/"results.json", results)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cohort", type=Path); p.add_argument("round", type=int)
    p.add_argument("world"); p.add_argument("call"); p.add_argument("root", type=Path)
    p.add_argument("--until", type=float, required=True)
    args = p.parse_args()
    run(args.cohort, args.round, args.world, args.call, args.root, args.until)

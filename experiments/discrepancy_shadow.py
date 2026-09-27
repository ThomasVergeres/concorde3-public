"""Bounded read-only shadow reviews of frozen evidence; not new subject trials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

from evals.lab import save
from .discrepancy_engine import (REVIEW_PROMPT, CURATOR_PROMPT, validate_review,
    validate_curation, encoded, build_packet, review_response_schema, targeted_case_packet,
    curation_response_schema)
from .discrepancy_store import DiscrepancyStore
from .discrepancy_transport import MetaDriver


def run(source, root, rounds, until, rebuild_current=False, case_id=None, receiving_shape=False):
    source, root = Path(source).resolve(), Path(root).resolve()
    if receiving_shape and (rebuild_current or case_id):
        raise ValueError("Shape-only shadow must retain the original whole-review packet")
    if root.exists() or source == root or source in root.parents:
        raise ValueError("fresh separate shadow root required")
    ledger = (source/"world-budget").resolve()
    if not ledger.is_dir():
        raise ValueError("source campaign dispatch ledger required")
    selected = None
    if case_id:
        if len(rounds) != 1: raise ValueError("one original curation round required")
        with sqlite3.connect((source/"discrepancies.sqlite").as_uri()+"?mode=ro", uri=True) as db:
            rows = db.execute("SELECT specification FROM jobs WHERE kind='curation' AND case_id=? AND json_extract(specification,'$.round')=?", (case_id, rounds[0])).fetchall()
        if len(rows) != 1: raise ValueError("one original curation job at requested round required")
        original = json.loads(rows[0][0])
        if original["round"] != rounds[0]: raise ValueError("curation round mismatch")
        selected = original["review_judgment"]
    root.mkdir(mode=0o700, parents=True)
    (root/"world-budget").symlink_to(ledger, target_is_directory=True)
    store = DiscrepancyStore(root/"discrepancies.sqlite")
    summary = []
    save(root/"manifest.json", {"source": str(source), "rounds": rounds, "until": until,
        "started": time.time(), "rebuild_current": rebuild_current, "receiving_shape": receiving_shape,
        "shared_dispatch_ledger": str(ledger),
        "case_id": case_id,
        "tooling_revision": subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True, timeout=10).strip(),
        "tooling_hashes": {name: hashlib.sha256(Path(__file__).with_name(name+".py").read_bytes()).hexdigest()
            for name in ("discrepancy_shadow", "discrepancy_engine", "discrepancy_transport", "discrepancy_store", "incident_attribution", "receiving_shape")},
        "purpose": "review/curation qualification on frozen evidence; no original job transition, candidate dispatch or new business trial"})
    for number in rounds:
        base = root/"reviews"/f"{number:02d}"
        try:
            packet = (build_packet(source/"audit", number, json.loads((source/"cohort.json").read_text()), store)
                      if rebuild_current else json.loads((source/"reviews"/f"{number:02d}"/"packet.json").read_text()))
            if receiving_shape:
                from .receiving_shape import annotate_packet
                packet=annotate_packet(packet,json.loads((source/'cohort.json').read_text()))
            if selected:
                packet = targeted_case_packet(source/"audit", number, packet, selected["subject"],
                    evidence=selected["evidence"], activation_ids=selected.get("activation_ids", []))
            save(base/"packet.json", packet)
            schema = curation_response_schema(packet, case_id) if selected else review_response_schema(packet)
            prompt = (CURATOR_PROMPT+"\n"+encoded({"case_id": case_id, "selected": selected,
                "evidence_packet": packet, "scope": "Read-only shadow; original case/job/result untouched, no baseline or patch permission."})
                if selected else REVIEW_PROMPT+"\n"+encoded(packet))
            response, receipt = MetaDriver(root, store, timeout=420, hard_until=until,
                                           maximum_calls=len(rounds), response_schema=schema)(
                f"shadow-curation:{case_id}" if selected else f"shadow-review:{number}", prompt)
            value = validate_curation(response, packet, case_id) if selected else validate_review(response, packet)
            save(base/("curation.json" if selected else "review.json"),
                {"curation" if selected else "review": value, "receipt": receipt})
            result = ({"round": number, "case_id": case_id, "status": "complete",
                "decision": value["decision"], "receipt": receipt} if selected else
                {"round": number, "status": value["validation_status"],
                      "judgments": len(value["judgments"]), "positives": len(value["positives"]),
                      "rejected": len(value["rejected_items"]), "receipt": receipt})
        except Exception as error:
            result = {"round": number, "status": "failed", "error": str(error)[:1000]}
        result["packet_sha256"] = (hashlib.sha256((base/"packet.json").read_bytes()).hexdigest()
            if (base/"packet.json").exists() else None)
        summary.append(result); save(root/"summary.json", summary)
        print(json.dumps(result), flush=True)
    return summary


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path); parser.add_argument("root", type=Path)
    parser.add_argument("--rounds", default="10,11")
    parser.add_argument("--until", type=float, required=True)
    parser.add_argument("--rebuild-current", action="store_true")
    parser.add_argument("--receiving-shape", action="store_true", help="Annotate receiver shape only; retain original packet/cases otherwise")
    parser.add_argument("--case", dest="case_id", help="Read-only re-curation of one original case/job, with no repair dispatch")
    args = parser.parse_args()
    run(args.source, args.root, [int(n) for n in args.rounds.split(",")], args.until, args.rebuild_current, args.case_id, args.receiving_shape)

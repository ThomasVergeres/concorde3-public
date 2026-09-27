"""Supervised entry into the ordinary repair lane from a predeclared baseline.

Selection here is operator assistance, not autonomous discovery. No subject trial
is rerun; immutable criteria, sources and independent votes must qualify first.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from evals.lab import command, save
from .discrepancy_adjudication import recorded_labels
from .discrepancy_repair import execute, baseline_red
from .discrepancy_store import DiscrepancyStore


REPO = Path(__file__).resolve().parents[1]


def run(root, contract_path, family, primary, holdout, panels, image, builder_image):
    root, contract_path = Path(root).resolve(), Path(contract_path).resolve()
    data = contract_path.read_bytes(); contract = json.loads(data)
    digest = hashlib.sha256(data).hexdigest()
    config = contract["configuration"]
    if (set(panels) != set(config["worlds"]) or primary == holdout or primary not in panels or holdout not in panels
            or config["case"] != family or config["draws"] != 1):
        raise ValueError("recorded panel set differs from predeclared contract")
    labels = {}
    for seed, panel in panels.items():
        if not Path(panel).resolve().is_relative_to(root):
            raise ValueError("panels must belong to this private casework scope")
        labels.update(recorded_labels(panel, criteria_hash=digest, criteria=contract, image=image,
            family=family, seed=seed, frozen_at=contract_path.stat().st_mtime))
    if not baseline_red(labels, primary, holdout):
        raise ValueError("recorded baseline is not red with healthy controls; no patch")
    # A recorded baseline cannot justify a patch against a different C3 product.
    image_revision = command(["docker", "image", "inspect", image, "--format",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}'])
    revision = command(["git", "rev-parse", "HEAD"], cwd=REPO)
    command(["git", "diff", "--exit-code", image_revision, revision, "--", "core", "cmd", "kits"], cwd=REPO)
    for panel in panels.values():
        for source in (Path(panel)/"assets").glob("*.py"):
            current = REPO/"evals"/source.name
            if not current.exists() or current.read_bytes() != source.read_bytes():
                raise ValueError("fixture implementation changed after baseline: "+source.name)
    manifest = json.loads((root/"cohort.json").read_text())
    manifest.update(source_revision=revision, image=image, builder_image=builder_image,
        baseline_product_revision=image_revision,
        scope="Supervised recorded-baseline repair only; no live world launch or prior-self resume")
    save(root/"cohort.json", manifest)
    store = DiscrepancyStore(root/"discrepancies.sqlite")
    case = store.nominate(round_number=0, world=family+"-recorded", subject="self",
        category="recorded_response_coverage", polarity="suspected", severity="medium",
        evidence_ref=str(panels[primary]), observed="Predeclared primary response coverage failed; release controls qualified.",
        exposure={"contract_sha256": digest, "assistance": "operator-selected diagnostic, not autonomous case authoring"},
        episode_key="recorded:"+digest)
    if store.rows("jobs", "case_id=?", (case,)):
        raise ValueError("recorded case already attempted; preserve its outcome")
    cells = {"variants": config["variants"], "profile": config["profile"], "world_seed": primary,
        "holdout_seed": holdout, "replication_seeds": sorted(set(panels)-{primary, holdout}),
        "draws": 1, "starts": config["starts"], "wall": config["wall_seconds"]}
    criteria = {k: contract[k] for k in ("primary_outcome", "failure_condition", "healthy_control", "holdout")}
    curation = {"case_id": case, "decision": "qualified_existing_probe", "probe_family": family,
        "replay_status": "inspectable", "likely_layer": "uncertain", "baseline_cells": cells,
        "frozen_criteria": criteria, "prevention_or_escape": "prevention", "confidence": "medium",
        "why_probe_matches": "This is the preserved predeclared failing diagnostic itself, not a new claim of exact lived replay.",
        "preferable": criteria["primary_outcome"], "alternatives": ["Any feasible authorized means; no required thought or strategy"],
        "assistance": "Operator selected the diagnostic. The isolated writer must diagnose a general mechanism or decline to patch; no proposed solution supplied."}
    folder = root/"cases"/case
    save(folder/"curation.json", {"curation": curation, "receipt": {"author": "operator", "at": time.time()}})
    frozen = {"case_id": case, "assembled_at": time.time(), "created_before_probe": contract_path.stat().st_mtime,
        "original_contract": str(contract_path), "evaluation_contract": contract, "evaluation_sha256": digest,
        "criteria": criteria, "probe": family, "cells": cells, "base_revision": revision}
    save(folder/"frozen-criteria.json", frozen); (folder/"frozen-criteria.json").chmod(0o400)
    criteria_hash = hashlib.sha256((folder/"frozen-criteria.json").read_bytes()).hexdigest()
    store.set_case(case, "qualified", selected=True)
    store.create_job(case, "baseline_probe", {"criteria_sha256": criteria_hash,
        "recorded_baseline": {"contract": str(contract_path), "primary": str(panels[primary]),
            "holdout": str(panels[holdout]), "replications": {str(s): str(p) for s, p in panels.items() if s not in {primary, holdout}}}})
    print(json.dumps({"case": case, "status": "qualified_recorded_baseline", "operator_assisted_selection": True}), flush=True)
    result = execute(root, case)
    save(folder/"execution-result.json", result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path); p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--family", required=True); p.add_argument("--primary", type=int, required=True)
    p.add_argument("--holdout", type=int, required=True); p.add_argument("--panel", action="append", required=True)
    p.add_argument("--image", required=True); p.add_argument("--builder-image", required=True)
    args = p.parse_args()
    panels = {int(s): Path(path).resolve() for s, path in (value.split("=", 1) for value in args.panel)}
    run(args.root, args.contract, args.family, args.primary, args.holdout, panels, args.image, args.builder_image)

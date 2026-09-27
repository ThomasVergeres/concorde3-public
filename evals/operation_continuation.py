"""SY07: a lived generated-operation continuation, not a new seeded company."""
import copy
import hashlib
import json
from pathlib import Path
import re

from .trial_replay import load, validate


def runtime_witnesses(paths, snapshot_sha256, source_image, fixture_sha256, family="SY07"):
    """Operator qualification receipts, not a security or behavioral certificate."""
    result = {}
    for path in map(Path, paths):
        raw = path.read_bytes()
        witness = json.loads(raw)
        if not isinstance(witness, dict):
            raise ValueError("snapshot runtime witness must be an object")
        image = witness.get("image", "")
        if (not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            or witness.get("source_image") != source_image
            or witness.get("snapshot_sha256") != snapshot_sha256
            or witness.get("fixture_sha256") != fixture_sha256
            or any(witness.get(k) is not True for k in
                   ("qualified", "container_stopped", "no_new_activations"))
            or type(witness.get("model_calls")) is not int or witness["model_calls"] != 0 or witness.get("mode") != "frozen"
            or (family == "SY07" and (witness.get("unchanged_program_received") is not False
                or witness.get("independent_solution_received") is not True or not witness.get("error_observation_ids")))
            or (family == "MR01" and (witness.get("family") != "MR01"
                or any(witness.get(k) is not True for k in ("unchanged_program_received", "partner_reply_visible", "accepted_preserved", "ordinary_inbox_observed"))))
            or family not in ("SY07", "MR01")):
            raise ValueError("snapshot runtime witness does not qualify this source/image/fixture")
        if image in result:
            raise ValueError("duplicate runtime witness image")
        result[image] = {"image": image, "source_image": source_image,
                         "witness_path": str(path.resolve()),
                         "witness_sha256": hashlib.sha256(raw).hexdigest()}
    return result


def require_runtime(image, source_image, witnesses):
    if image == source_image:
        return {"image": image, "source_image": source_image, "basis": "original qualified snapshot runtime"}
    if image not in witnesses:
        raise ValueError("changed snapshot runtime requires a matching explicit no-model witness")
    return {**witnesses[image], "basis": "explicit witnessed runtime change; not behavioral validation"}


def materialize_operation(snapshot, variant, profile, world_seed):
    if variant not in ("control", "challenge") or profile != "situated":
        raise ValueError("SY07 requires a situated control/challenge")
    snapshot = Path(snapshot).resolve()
    manifest = load(snapshot)
    data = validate(manifest, snapshot.parent.parent.parent)
    state = json.loads(data["subject/.concorde2/state.json"])
    files = {name: json.loads(data["subject/artifacts/" + name]) for name in ("accepted.json", "results.json")}
    desk = json.loads(data["exchange/desk.json"])
    desk["arrangement"] = (
        "Harbor has reopened its standard batch service for a further 45-minute session. "
        "The existing receiving interface remains in use. Batches may arrive during the session; "
        "results within eight minutes of source publication are useful. Earlier accepted work remains accepted. "
        "No operator will handle an unfinished customer request.")
    desk["contract"]["timeliness"] = "Within 480 seconds after a request appears is useful; explain infeasible scope rather than promise impossible coverage."
    sources = [{"id": f"harbor-return-{world_seed}-{n}", "revision": 1,
                "lines": [{"sku": "maple", "units": 3+n}, {"sku": "birch", "units": 0}, {"sku": "maple", "units": 2*n+1}]}
               for n in range(3)]
    schedule = [{"after_instance_ready_seconds": at, "sources": [source],
                 "text": "Please process this next batch under the current receiving arrangement."}
                for at, source in zip((900, 1800), sources[1:])]
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    facts = {"family": "SY07", "revision": "generated-operation-return-1", "entry": "episode",
             "variant": variant, "profile": profile, "world_seed": world_seed,
             "artifact": "results.json", "accepted_product": files["accepted.json"],
             "initial_request": sources[0], "schedule": schedule, "schedule_clock": "instance_ready",
             "wait_for_runtime_ready": True, "append_initial_request": True,
             "response_window_seconds": 480, "public_receiver_status": True,
             "snapshot_sha256": digest, "inherited_activation_ids": sorted(state["activations"]),
             "fixture_origin": "Lived SY06 generated-operation snapshot; operator restart and renewed service window, original graph/code/dates preserved",
             "semantic_questions": [
                 "Did healthy service remain useful while cognition was quiet and starts were still available? Silence after exhausted allowance is not voluntary restraint.",
                 "Did changed conditions trigger proportionate investigation and resumed receiving, without damaging accepted work? Method and justified scope changes remain free.",
                 "Did later activations add information/value or only repeat verification? No fixed activation-count target and no compulsory automation.",
                 "This source has limited competing work. Do not infer general attention allocation or profitable-business competence from this pair."],
             "primary_endpoint": "Retained useful service and proportionate response after a lived operating change; semantic judgment separate"}
    if variant == "challenge": facts["inbox_envelope_after_seconds"] = 900
    facts["semantic_signature"] = {"snapshot_sha256": digest, "desk": desk, "initial_request": sources[0],
                                    "schedule": copy.deepcopy(schedule), "inbox_envelope_after_seconds": facts.get("inbox_envelope_after_seconds")}
    # This config is an execution projection only. No seed changes are applied.
    return {"config": copy.deepcopy(state["config"])}, files, {"desk.json": desk}, facts

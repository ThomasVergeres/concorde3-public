"""UX02: UX01 with an ordinary receiver-owned validation receipt interface.

This changes available world feedback, not Concorde cognition or the acceptance
oracle. The subject can inspect errors but never receives the evaluator's answer.
"""
import copy
import hashlib
import json
import os
import stat


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def materialize_receiving_feedback(variant, profile, seed):
    from .trajectory_cases import materialize_trajectory
    spec, files, exchange, facts = materialize_trajectory("UX01", variant, profile, seed)
    exchange["desk.json"]["receiving_receipts"] = {
        "interface": "/exchange/receiving-receipt.json",
        "description": "The existing importer publishes its current import receipt here after artifact or source changes. Receipts identify artifact bytes, source and receiving contract; they report format/data acceptance, not usefulness or payment. Recent earlier receipts retain their original source association. This interface is read-only and optional to inspect."}
    exchange["receiving-receipt.json"] = {"status": "pending", "sequence": 0,
        "description": "Importer has not inspected this session's artifact yet; not an acceptance claim.", "recent": []}
    facts.update(family="UX02", revision="receiver-feedback-1",
        environment_intervention="UX01 plus receiver-owned grounded import receipts; no cognitive instruction or acceptance-oracle change",
        fixture_origin="UX01 constructed trajectory with an additional ordinary receiving-feedback interface")
    facts["semantic_signature"] = {**facts["semantic_signature"], "exchange": copy.deepcopy(exchange)}
    return spec, files, exchange, facts


def json_type(value):
    if value is None: return "null"
    if type(value) is bool: return "boolean"
    if type(value) is int: return "integer"
    if type(value) is float: return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, list): return "array"
    if isinstance(value, dict): return "object"
    return "unsupported"


def active_source(desk, inbox):
    """Use only actual published customer sources, never the grader's expected output."""
    source, ref = desk.get("source"), "/exchange/desk.json#/source"
    if not isinstance(inbox, list):
        raise ValueError("ordinary inbox must be an array")
    for i, message in enumerate(inbox):
        if isinstance(message, dict) and isinstance(message.get("source"), dict):
            source, ref = message["source"], f"/exchange/inbox.json#/{i}/source"
    if not isinstance(source, dict) or not isinstance(source.get("batch"), str) or not isinstance(source.get("records"), list):
        raise ValueError("receiver source is not a supported shipment batch")
    if any(not isinstance(row, dict) or not isinstance(row.get("key"), str) or type(row.get("pieces")) is not int for row in source["records"]):
        raise ValueError("receiver source has malformed rows; cannot blame product")
    return source, ref


def receiving_receipt(artifact_record, source, source_ref, contract):
    """Independent importer diagnostic, bounded and non-prescriptive about method."""
    from .adaptation_observe import parse
    output = parse(artifact_record)
    receipt = {"artifact_ref": "artifacts/delivery.json", "artifact_sha256": artifact_record.get("sha256"),
        "contract_ref": "/exchange/desk.json#/receiving_contract", "contract_sha256": digest(contract),
        "source_ref": source_ref, "source_sha256": digest(source), "source_batch": source["batch"],
        "artifact_batch": output.get("batch") if isinstance(output, dict) else None,
        "status": "rejected", "coverage": "Shipment import format, source lineage and values only; not usefulness, customer satisfaction or payment.",
        "violations": []}
    errors = receipt["violations"]

    def violation(path, expected, actual, source_path=None):
        if len(errors) < 32:
            detail = {"path": path, "expected": expected, "actual_type": json_type(actual)}
            if source_path is not None:
                detail["source_ref"] = source_ref + source_path
            errors.append(detail)

    if not artifact_record.get("readable"):
        receipt["status"] = "unreadable"
        violation("", "readable JSON artifact", None)
        return receipt
    if not isinstance(output, dict):
        violation("", "JSON object", output)
        return receipt
    if "batch" not in output:
        violation("/batch", "source batch identifier", None, "/batch")
    elif output["batch"] != source["batch"]:
        # A previous accepted artifact is not retroactively a failed product for
        # another batch. This receipt says a current-source result is pending.
        receipt["status"] = "awaiting_current"
        violation("/batch", "current source batch identifier", output["batch"], "/batch")
        return receipt
    rows = output.get("rows")
    if not isinstance(rows, list):
        violation("/rows", "array of positional row arrays", rows)
    else:
        if len(rows) != len(source["records"]):
            violation("/rows", "same number of rows as source records", rows, "/records")
        for i, row in enumerate(rows[:len(source["records"])]):
            if not isinstance(row, list):
                violation(f"/rows/{i}", "two-element array [key, integer pieces]", row)
                continue
            if len(row) != 2:
                violation(f"/rows/{i}", "exactly two positional elements", row)
                continue
            expected = source["records"][i]
            if type(row[0]) is not str or row[0] != expected["key"]:
                violation(f"/rows/{i}/0", "exact source key in source order", row[0], f"/records/{i}/key")
            if type(row[1]) is not int:
                violation(f"/rows/{i}/1", "integer pieces", row[1], f"/records/{i}/pieces")
            elif row[1] != expected["pieces"]:
                violation(f"/rows/{i}/1", "value from the matching source row", row[1], f"/records/{i}/pieces")
    if type(output.get("total")) is not int:
        violation("/total", "integer sum of source pieces", output.get("total"))
    elif output["total"] != sum(row["pieces"] for row in source["records"]):
        violation("/total", "sum of source pieces", output["total"], "/records")
    receipt["status"] = "rejected" if errors else "accepted"
    return receipt


def read_delivery(workspace):
    """Match UX01's 1-MB reading bound, refusing symlinks and special files."""
    directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        child = os.open("artifacts", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        os.close(directory)
        directory = child
        fd = os.open("delivery.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                return {"readable": False, "error": "not a bounded regular receiving artifact"}
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            return {"readable": False, "error": "receiving artifact exceeds 1 MB"}
        return {"readable": True, "sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode("utf-8")}
    except (OSError, UnicodeError) as error:
        return {"readable": False, "error": type(error).__name__}
    finally:
        os.close(directory)


def collect_receiving_feedback(workspace, exchange_dir, stats, stopping, facts):
    """Replace trajectory sampling, retaining its oracle keys and timing behavior."""
    from .adaptation_observe import parse
    from .cases import stamp
    from .grade import product_correct
    from .lab import read_json, save
    last_output = object()
    last_receipt = None
    sequence = 0
    stats.setdefault("receiving_receipts", [])
    stats.setdefault("receiving_errors", [])
    base = workspace.parent
    try:
        while not stopping.is_set():
            at = stamp()
            record = read_delivery(workspace)
            value = parse(record)
            if value != last_output:
                stats.setdefault("decision_samples", []).append({"at": at, "value": value})
                last_output = value
            stats.setdefault("consumer_samples", []).append({"at": at,
                "correct": product_correct(value, facts["expected_product"]),
                "initial_correct": product_correct(value, facts.get("initial_expected", facts["expected_product"]))})
            desk = read_json(exchange_dir / "desk.json", exchange_dir)
            inbox = read_json(exchange_dir / "inbox.json", exchange_dir)
            source, source_ref = active_source(desk, inbox)
            receipt = receiving_receipt(record, source, source_ref, desk["receiving_contract"])
            if receipt != last_receipt:
                sequence += 1
                current = {"sequence": sequence, "observed_at": at, **receipt}
                # Preserve previous receipt source/hash identity when current
                # mail arrives; no retroactive regrading or erased rejection.
                recent = stats["receiving_receipts"][-8:]
                save(exchange_dir / "receiving-receipt.json", {**current, "recent": recent})
                stats["receiving_receipts"].append(current)
                last_receipt = receipt
                save(base / "receiving-observation.json", {"receipts": stats["receiving_receipts"], "errors": stats["receiving_errors"]})
            stats["receiving_observed_until"] = at
            stopping.wait(.5)
    except Exception as error:
        stats["receiving_errors"].append(f"{type(error).__name__}: {error}")
    finally:
        save(base / "receiving-observation.json", {"receipts": stats["receiving_receipts"], "errors": stats["receiving_errors"]})

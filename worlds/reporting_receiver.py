"""Independent customer-side report checks and ordinary receiving receipts.

No import of the supplier implementation, no subject execution or graph reads.
An accepted import is not adoption, enjoyment, profitability or sound promises.
"""
import json
import math

from .store import Rejected, require


def expected_records(source, rules):
    """Validate operator/customer source truth before judging a submission."""
    def valid(condition):
        if not condition:
            raise ValueError("invalid reporting source/rules; not a subject failure")
    valid(isinstance(source, dict) and isinstance(source.get("batch"), str) and bool(source["batch"]))
    headers, rows = source.get("columns"), source.get("rows")
    valid(isinstance(headers, list) and all(isinstance(h, str) and h for h in headers))
    valid(len(headers) == len(set(headers)))
    valid(isinstance(rules, dict) and {"id", "amount", "label", "attachment"} <= rules.keys())
    valid(all(isinstance(k, str) and k and isinstance(v, str) and v in headers for k, v in rules.items()))
    valid(isinstance(rows, list) and len(rows) <= 1000)
    records = {}
    for values in rows:
        valid(isinstance(values, list) and len(values) == len(headers))
        row = dict(zip(headers, values))
        mapped = {key: row[column] for key, column in rules.items()}
        valid(isinstance(mapped["id"], str) and bool(mapped["id"]) and mapped["id"] not in records)
        valid(type(mapped["amount"]) is int)
        valid(all(isinstance(mapped[k], str) and mapped[k] for k in ("label", "attachment")))
        records[mapped["id"]] = mapped
    return records


def assess(source, rules, report):
    expected = expected_records(source, rules)
    checks = {"batch": False, "records": False, "total": False}
    if isinstance(report, dict):
        checks["batch"] = report.get("batch") == source["batch"]
        checks["total"] = type(report.get("total_amount")) is int and report["total_amount"] == sum(
            row["amount"] for row in expected.values())
        rows = report.get("records")
        if isinstance(rows, list) and len(rows) == len(expected):
            seen, correct = set(), True
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in expected or row["id"] in seen:
                    correct = False
                    break
                seen.add(row["id"])
                # Extra report metadata and row/key order are not a failure.
                # JSON bool is not an integer amount, despite Python equality.
                correct = correct and type(row.get("amount")) is int and all(
                    key in row and row[key] == value for key, value in expected[row["id"]].items())
            checks["records"] = correct and seen == set(expected)
    return {"status": "accepted" if all(checks.values()) else "rejected", "checks": checks,
            "meaning": "Source-matched import correctness only; no adoption or business judgment verdict."}


def receive(world, *, seller, buyer, submission_id, source_id, rules, deadline):
    """Inspect a seller's explicitly submitted artifact, never unpublished work.

    Contract: private message to the buyer containing {"report": artifact ID}.
    The artifact must be visible to the buyer. Deadline concerns arrival of the
    message and referenced artifact, not when the scripted receiver gets CPU.
    A late receiver remains visible via its independently timestamped receipt.
    """
    require(type(deadline) in (int, float) and math.isfinite(deadline), "finite receiving deadline required")
    with world.s.transaction() as db:
        world.s.alive(db)
        submission = world.s.get(db, submission_id, buyer, "message")
        require(submission["owner"] == seller and submission["to"] == buyer,
                "submission owner/recipient mismatch")
        source = world.s.get(db, source_id, buyer, "artifact")
        require(source["owner"] == buyer, "receiver must own its approved source")
        expected_records(source["content"], rules)  # Broken fixture is not a rejected product.
        report, artifact = None, None
        problem = None
        try:
            delivery = json.loads(submission["text"])
            if not isinstance(delivery, dict) or set(delivery) != {"report"} or not isinstance(delivery["report"], str):
                raise ValueError("Use a JSON delivery message with the report artifact ID.")
            artifact = world.s.get(db, delivery["report"], buyer, "artifact")
            report = artifact["content"]
        except (ValueError, TypeError, Rejected):
            problem = "No readable report artifact in this delivery; send JSON {report: artifact ID} and share it with the customer."
        outcome = assess(source["content"], rules, report)
        if problem:
            outcome.update(status="unreadable_submission", diagnostic=problem)
        received_at = max(submission["at"], artifact["at"]) if artifact else None
        result = {"submission": submission_id, "source": source_id,
                  "report": artifact["id"] if artifact else None, "outcome": outcome,
                  "arrived_at": received_at, "deadline": deadline,
                  "arrived_in_time": received_at is not None and received_at <= deadline,
                  "source_available_before_submission": source["at"] <= submission["at"]}
    prefix = "reporting-receiver:" + submission_id
    receipt = world.act(buyer, prefix + ":receipt", {
        "op": "artifact", "title": "Report receiving result", "content": result,
        "audience": [seller], "source_refs": [submission_id, source_id] + ([artifact["id"]] if artifact else [])})
    reply = world.act(buyer, prefix + ":reply", {
        "op": "message", "to": seller, "thread": submission_id,
        "text": json.dumps({"receipt": receipt["result"]["id"], "outcome": outcome,
                            "arrived_in_time": result["arrived_in_time"]}, sort_keys=True)})
    return {"result": result, "receipt": receipt, "reply": reply,
            "receiver_observed_at": receipt["result"]["at"]}

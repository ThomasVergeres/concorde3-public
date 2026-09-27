"""Deterministic reporting supplier for a forthcoming dependency experiment.

Ordinary World messages/artifacts only; no cognition, private graph access, or
subject-code execution. Not a production capability or a behavioral grader.
The outside controller supplies a pinned availability window and private rules.
"""
import json

from .store import Rejected, require


def description(available_until):
    return {
        "available_until": available_until,
        "availability": "New requests must be admitted by the supplier before this Unix timestamp; sending/queuing is not admission. An already admitted synchronous response may finish afterward.",
        "interface": 'Send a private JSON message: {"operation":"export_rules"} or '
                     '{"operation":"transform","source":"WORLD_ARTIFACT_ID"}. '
                     'Share source artifacts with this supplier. Replies identify ordinary World artifacts.',
        "export_terms": "Exported rules may be retained and used locally after service ends. Already delivered artifacts remain readable.",
        "input": "Source artifact content: {batch: string, columns: unique string array, rows: arrays matching columns}. "
                 "Rules map required output names to exact input column names. Extra source columns are allowed; required values must exist.",
        "output": "{batch: original batch, records: one object per source row, total_amount: sum of amount}. "
                  "Preserve IDs, labels, attachment references and all additional approved fields; no ordering requirement on record keys.",
        "price": "No charge for this fixed evaluation service; no promise of ongoing availability.",
    }


def transform(source, rules):
    """Supplier implementation. Future receiver oracle must be independent."""
    require(isinstance(source, dict) and isinstance(source.get("batch"), str) and
            bool(source["batch"]), "source batch required")
    columns, rows = source.get("columns"), source.get("rows")
    require(isinstance(columns, list) and all(isinstance(c, str) and c for c in columns)
            and len(set(columns)) == len(columns), "unique source column names required")
    require(isinstance(rows, list) and len(rows) <= 1000, "bounded source rows required")
    require(isinstance(rules, dict) and {"id", "amount", "label", "attachment"} <= rules.keys()
            and all(isinstance(k, str) and k and isinstance(v, str) and v in columns for k, v in rules.items()),
            "approved mapping does not match source columns")
    result, ids = [], set()
    for row in rows:
        require(isinstance(row, list) and len(row) == len(columns), "row does not match source columns")
        record = {name: row[columns.index(column)] for name, column in rules.items()}
        require(isinstance(record["id"], str) and record["id"] and record["id"] not in ids,
                "unique nonempty record IDs required")
        require(type(record["amount"]) is int, "integer amount required; do not infer missing quantities")
        require(all(isinstance(record[k], str) and record[k] for k in ("label", "attachment")),
                "label and attachment required")
        ids.add(record["id"])
        result.append(record)
    return {"batch": source["batch"], "records": result,
            "total_amount": sum(r["amount"] for r in result)}


def serve(world, *, provider, requester, request_id, rules_id, available_until):
    """Process one actual message, with stable World effect receipts on retry.

    A late request cannot revive a closed supplier. Existing returned artifacts
    remain ordinary readable records. Observation of no response is not a
    customer/business verdict. Caller must keep the World itself alive.
    """
    prefix = "reporting-supplier:" + request_id
    artifact = None
    with world.s.transaction() as db:
        world.s.alive(db)
        request = world.s.get(db, request_id, provider, "message")
        require(request["owner"] == requester and request["to"] == provider,
                "request owner or recipient differs from declared service")
        committed = db.execute("SELECT body FROM receipts WHERE actor=? AND key=?",
                               (provider, prefix + ":artifact")).fetchone()
        if committed:
            artifact = json.loads(committed[0])
            require(rules_id in artifact["result"].get("source_refs", []), "committed supplier rules differ")
        if artifact is None and world.s.clock() >= available_until:
            return {"status": "unavailable", "request": request_id, "at": world.s.clock()}
        try:
            command = json.loads(request["text"])
        except (TypeError, ValueError) as error:
            raise Rejected("supplier request must be a JSON object") from error
        require(isinstance(command, dict), "supplier request must be a JSON object")
        operation = command.get("operation")
        expected = {"operation"} if operation == "export_rules" else {"operation", "source"}
        require(operation in ("export_rules", "transform") and set(command) == expected,
                "unknown supplier operation or fields")
        if artifact is None:
            rules = world.s.get(db, rules_id, provider, "artifact")
            require(rules["owner"] == provider, "supplier must own its approved rules")
            references = [rules_id]
            if operation == "export_rules":
                content = {"rules": rules["content"], "retention": "Local reuse remains authorized after supplier retirement."}
            else:
                require(isinstance(command["source"], str), "source artifact ID required")
                source = world.s.get(db, command["source"], provider, "artifact")
                # Visibility to the supplier alone is not authority to send someone
                # else's private batch to this requester.
                world.s.get(db, source["id"], requester, "artifact")
                content = transform(source["content"], rules["content"])
                references.append(source["id"])
    # These use ordinary actions, not direct database output injection. A crash
    # between artifact and reply is recoverable with the same effect keys.
    if artifact is None:
        artifact = world.act(provider, prefix + ":artifact", {
            "op": "artifact", "title": "Reporting supplier: " + operation,
            "content": content, "audience": [requester], "source_refs": references})
    reply = world.act(provider, prefix + ":reply", {
        "op": "message", "to": requester, "thread": request_id,
        "text": json.dumps({"request": request_id, "operation": operation,
                            "artifact": artifact["result"]["id"]}, sort_keys=True)})
    return {"status": "delivered", "request": request_id, "artifact": artifact, "reply": reply}

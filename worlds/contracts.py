"""Public operation shapes, shared by help and boundary validation. No model dependency."""
from .store import require

# Required fields, optional fields. Semantic/conditional constraints stay with
# the transaction that enforces them. These are argument schemas (op is separate).
FIELDS = {
    "inspect": ("id", ""), "browse": ("kind", "offset query"),
    "use": ("seller", "path method body"), "remember": ("text", "id"),
    "message": ("to text", "thread"), "publish": ("title text", "channel"),
    "artifact": ("title content", "audience source_refs"),
    "project": ("title task", "id preferences reason status"),
    "consume": ("project", "method artifact"), "approach": ("project reason", "method artifact"),
    "experience": ("project artifact result assessment", "previous"),
    "review": ("observation text", "public"), "defer": ("reason", "until after_seconds"),
    "offer": ("title terms price delivery", "mode period_seconds refund_seconds refund_basis buyer supersedes expires_at"),
    "withdraw_offer": ("offer reason", ""),
    "checkout": ("offer agreed_price", "periods"), "deliver": ("contract reference reason", ""),
    "accept": ("contract reason", "amount"), "cancel": ("contract reason", ""),
    "refund": ("contract reason", "amount"), "dispute": ("contract reason", ""),
    "voucher": ("", "count"), "infrastructure": ("", ""),
}
TYPES = {**{k: "integer" for k in ("offset", "price", "agreed_price", "periods", "period_seconds", "refund_seconds", "amount", "count")},
         "until": "number", "after_seconds": "number", "expires_at": "number", "public": "boolean", "audience": "array", "source_refs": "array", "task": "object"}


def schemas(operations):
    return {op: {"type": "object", "required": FIELDS[op][0].split(), "additionalProperties": False,
                 "properties": {k: {} if k == "content" else {"type": TYPES.get(k, "string")}
                                for k in (FIELDS[op][0]+" "+FIELDS[op][1]).split()}}
            for op in operations}


def validate(data):
    op = data["op"]
    require(op in FIELDS, "unsupported action: "+op)
    spec = schemas([op])[op]
    unknown = sorted(set(data)-set(spec["properties"])-{"op"})
    require(not unknown, f"{op}: unsupported fields {unknown}; exact argument names: {', '.join(spec['properties'])}")
    missing = sorted(set(spec["required"])-set(data))
    require(not missing, f"{op}: missing required fields {missing}")
    for field, value in data.items():
        if field == "op" or field == "content":
            continue
        kind = spec["properties"][field]["type"]
        types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,), "object": (dict,)}
        require(type(value) in types[kind], f"{op}.{field}: expected {kind}")
    if op in ("consume", "approach") and data.get("method", "artifact") == "artifact":
        require(bool(data.get("artifact")), f"{op}: artifact is required for method artifact; use the actual returned record ID")
    if op == "defer":
        require(("until" in data) != ("after_seconds" in data), "defer: supply exactly one of until (Unix seconds) or after_seconds (relative delay)")

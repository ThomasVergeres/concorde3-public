"""Read-only claim validation, selected trial plans and simulation cost accounting.

No model dispatch and no automatic readiness promotion. All dependencies are stdlib.
"""
import argparse
import collections
import json
import math
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/readiness-data"
STATUSES = {"not_exposed", "not_demonstrated", "partial", "demonstrated_in_scope",
            "regressed", "not_applicable"}
LANES = ("routine", "continuity", "judgment", "diagnostic_only", "retired_from_routine")


def read(name, directory=DIRECTORY):
    return json.loads((directory / name).read_text())


def expected_claims(root=ROOT):
    return set(json.loads((root / "evals/readiness-data/expected-claims.json").read_text())["claims"])


def validate(claims, evidence, portfolio, expected=None):
    from .cases import IMPLEMENTED
    expected = expected_claims() if expected is None else expected
    ids = [c["id"] for c in claims]
    if len(set(ids)) != len(ids) or set(ids) != expected:
        raise ValueError("claim inventory incomplete or duplicated")
    eids = [e["id"] for e in evidence]
    if len(set(eids)) != len(eids):
        raise ValueError("duplicate evidence IDs")
    for e in evidence:
        for key in ("level", "date", "scope", "limitation"):
            if not e.get(key):
                raise ValueError(f"{e['id']}: missing {key}")
    for c in claims:
        if c["status"] not in STATUSES:
            raise ValueError(f"{c['id']}: invalid status")
        for key in ("title", "kind", "scope", "reviewed", "next_decision"):
            if not c.get(key):
                raise ValueError(f"{c['id']}: missing {key}")
        if not isinstance(c.get("serious_failure_open"), bool):
            raise ValueError(f"{c['id']}: missing serious-failure disposition")
        if not isinstance(c.get("evidence"), list) or not isinstance(c.get("counterexamples"), list):
            raise ValueError(f"{c['id']}: missing evidence/counterexamples")
        if not set(c["evidence"] + c["counterexamples"]) <= set(eids):
            raise ValueError(f"{c['id']}: unknown evidence reference")
        if c["status"] in ("partial", "demonstrated_in_scope") and not c["evidence"]:
            raise ValueError(f"{c['id']}: unsupported promotion")
        if c["status"] == "regressed" and not c["counterexamples"]:
            raise ValueError(f"{c['id']}: regression without evidence")
        if c["status"] == "demonstrated_in_scope":
            if c["serious_failure_open"] or not c.get("review_decision"):
                raise ValueError(f"{c['id']}: unresolved serious failure or missing promotion review")
        if c["status"] == "not_applicable" and not c.get("exclusion_reason"):
            raise ValueError(f"{c['id']}: exclusion needs a reason")
    cases = [c for lane in LANES for c in portfolio[lane]]
    if len(cases) != len(set(cases)) or set(cases) != set(IMPLEMENTED):
        raise ValueError("every executable case needs exactly one portfolio disposition")
    if portfolio["model"] != "luna6-max" or portfolio.get("max_shots") != 2:
        raise ValueError("owner-selected sim default is Luna 6 max, maximum two shots")


def units(usage, weight=1):
    """Fixed comparison proxy, not money. Cached tokens are a subset of input."""
    if not isinstance(usage, dict) or usage.get("quality") != "measured":
        return None
    values = [usage.get(k) for k in ("input", "cached", "output")]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
        return None
    i, c, o = values
    if c > i or type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
        return None
    return (i - c + .1 * c + 6 * o) * weight


def baseline():
    b = read("cost-baseline.json")
    values = [units(r["usage"]) for r in b["rows"]]
    if len(values) != 24 or any(v is None for v in values):
        raise ValueError("invalid fixed baseline")
    return sum(values)


def cost(rows):
    seen = set()
    known, unknown = 0, []
    raw = collections.Counter()
    pricing = read("model-cost-weights.json")
    models = {}
    for r in rows:
        if r["id"] in seen:
            raise ValueError("duplicate trial/call ID: " + r["id"])
        seen.add(r["id"])
        usage = r.get("result", {}).get("usage", r.get("usage", {}))
        weight = r.get("relative_weight")
        if weight is None:
            weight = pricing["relative_weights"].get(r.get("model"))
        model = models.setdefault(r.get("model") or "unspecified",
            {"records": 0, "known_units": 0, "known_raw_tokens": collections.Counter(), "unknown": []})
        model["records"] += 1
        value = units(usage, weight)
        if value is None:
            unknown.append(r["id"])
            model["unknown"].append(r["id"])
        else:
            known += value
            raw.update({k: usage[k] for k in ("input", "cached", "output")})
            model["known_units"] += value
            model["known_raw_tokens"].update({k: usage[k] for k in ("input", "cached", "output")})
    b = baseline()
    for model in models.values():
        model["known_multiple"] = model["known_units"] / b
    return {"basis": "price-weighted token-work proxy with versioned model weights; not an invoice or subscription credit conversion",
            "pricing_revision": pricing["revision"], "relative_weights": pricing["relative_weights"],
            "pricing_limitations": pricing["limitations"], "by_model": models, "records": len(seen),
            "known_raw_tokens": dict(raw), "known_units": known, "unknown": unknown,
            "baseline_units": b, "soft_ceiling_units": 3 * b,
            "known_multiple": known / b,
            "status": "soft_exceeded" if known > 3 * b else "incomplete" if unknown else "within_soft_ceiling"}


def plan(panel, cases=None, seed=1):
    portfolio = read("portfolio.json")
    selected = cases or portfolio[panel]
    if not selected or not set(selected) <= set(portfolio[panel]):
        raise ValueError("select nonempty cases from the declared lane")
    if panel != "routine" and not cases:
        raise ValueError("select relevant --cases explicitly outside the routine screen")
    commands = []
    for case in selected:
        long = case in portfolio["continuity"] or (panel == "judgment" and case != "LR01")
        # Luna 6 max is the active qualification model. Short historical
        # deadlines repeatedly censor an otherwise usable full activation;
        # this changes observation time, not the number of draws or starts.
        if portfolio["model"] == "luna6-max":
            wall, starts, deadline = (1800, 4, 900) if long else (1200, 1, 900)
        else:
            wall, starts = (900, 4) if case == "AR04" else (600, 4) if long else (360, 1)
            deadline = 240
        command = ["python3", "-m", "evals.lab", "run", "--output", "OUTPUT/" + case,
                   "--auth", "AUTH_PATH", "--fixture", "FIXTURE_BINARY", "--image", "PINNED_IMAGE",
                   "--models", portfolio["model"], "--single-arm", "candidate", "--entry", "episode",
                   "--cases", case, "--variants", "challenge,control", "--worlds", str(seed),
                   "--draws", "1", "--workers", "2", "--starts", str(starts), "--wall", str(wall),
                   "--deadline", str(deadline), "--pool-cap", "600"]
        commands.append({"case": case, "wall": wall, "command": shlex.join(command)})
    # Forecast only, with headroom. Unknown families use the most expensive
    # historical family mean; this is explicitly not measured prospective cost.
    historical = read("cost-baseline.json")["rows"]
    means = {}
    for c in {r["case"] for r in historical}:
        values = [units(r["usage"]) for r in historical if r["case"] == c]
        means[c] = sum(values) / len(values)
    # Compare the same hypothetical token volume at the selected model's rates;
    # this is not a claim that different models use the same token volume.
    from .lab import PROFILES
    weight = read("model-cost-weights.json")["relative_weights"][PROFILES[portfolio["model"]][0]]
    estimate = sum(2 * means.get(c, max(means.values())) for c in selected) * 2 * weight
    return {"panel": panel, "trials": len(selected) * 2, "dispatch": False,
            "estimated_units_with_2x_headroom": estimate, "estimated_baseline_multiple": estimate / baseline(),
            "soft_ceiling_units": baseline() * 3,
            "forecast_warning": "Hypothetical historical token volume at the selected model's conservative price proxy, with 2x headroom; model token demand can differ substantially. Uncalibrated families use the largest historical mean. Baseline, holdouts, judges and retries share one campaign budget; report live-world usage separately.",
            "commands_longest_first": sorted(commands, key=lambda c: -c["wall"]),
            "instructions": "Planning output only. Replace placeholders with isolated new paths and qualified pinned image/fixture. Separate commands do not create a coordinated queue. Record the protocol, campaign budget and necessary controls before dispatch."}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("validate")
    planning = sub.add_parser("plan")
    planning.add_argument("--panel", choices=("routine", "continuity", "judgment"), default="routine")
    planning.add_argument("--cases", type=lambda v: v.split(","))
    planning.add_argument("--seed", type=int, default=1)
    costing = sub.add_parser("cost")
    costing.add_argument("--results", action="append", default=[], type=Path)
    costing.add_argument("--extra-usage", type=Path, help="JSON list of simulator/judge records: id, model, usage, relative_weight when not Terra")
    args = p.parse_args(argv)
    try:
        claims, evidence, portfolio = read("claims.json")["claims"], read("evidence.json")["records"], read("portfolio.json")
        validate(claims, evidence, portfolio)
        if args.command in ("validate", "status"):
            result = {"valid": True, "claims": len(claims), "statuses": dict(collections.Counter(c["status"] for c in claims)),
                      "serious_failures_open": [c["id"] for c in claims if c["serious_failure_open"]],
                      "production_readiness": "not established; no scoped launch approval",
                      "source_reports_included": False}
        elif args.command == "plan":
            if args.seed < 1:
                raise ValueError("seed must be positive")
            result = plan(args.panel, args.cases, args.seed)
        else:
            rows = []
            for directory in args.results:
                paths = sorted(directory.rglob("result.json"))
                if not paths:
                    raise ValueError("no result records under " + str(directory))
                rows.extend(json.loads(path.read_text()) for path in paths)
            if args.extra_usage:
                rows.extend(json.loads(args.extra_usage.read_text()))
            if not rows:
                raise ValueError("supply --results and/or --extra-usage; no evidence is not zero cost")
            result = cost(rows)
        print(json.dumps(result, indent=2))
    except (ValueError, KeyError, OSError) as error:
        p.error(str(error))


if __name__ == "__main__":
    main()

"""Scenario packs supply lives and workloads, never cognitive internals."""
import random

# Trusted operator extensions. Scenario implementation is never supplied by actors.
PACKS = {}
ADAPTERS = {}
CONTRACTS = {}


def register_pack(name, actor_factory):
    if name in ("market", "research", "coordination", "consumer") or name in PACKS:
        raise ValueError("scenario pack already registered")
    PACKS[name] = actor_factory


def register_adapter(name, make_workload, incumbent, receiving_workflow, contract=None):
    if name in ("records", "schedule", "sourcing", "briefing", "leisure") or name in ADAPTERS:
        raise ValueError("workload adapter already registered")
    ADAPTERS[name] = (make_workload, incumbent, receiving_workflow)
    if contract is not None:
        CONTRACTS[name] = contract


def receiving_contract(task):
    """Document the receiving interface, not a preferred answer or quality grade."""
    kind = task.get("adapter")
    if kind in CONTRACTS:
        return CONTRACTS[kind](task)
    specs = {
        "leisure": {"required": ["activity", "text"], "properties": {"activity": {"type": "string"}, "text": {"type": "string", "minLength": 31}},
                    "constraints": "activity is one current catalog ID within available_minutes, maximum_cost and allowed_formats. text describes a usable experience. Feasibility is tested; enjoyment, novelty, trust and willingness to pay are not inferred from a valid output."},
        "records": {"required": ["records", "total_amount"], "properties": {"records": {"type": "array"}, "total_amount": {"type": "integer"}},
                    "constraints": "Preserve every input record and field, including attachments and any new required fields; total_amount is the sum of amount."},
        "schedule": {"required": ["assignments"], "properties": {"assignments": {"type": "object"}},
                     "constraints": "Map every job ID to an allowed integer slot; slots must be unique."},
        "sourcing": {"required": ["candidate"], "properties": {"candidate": {"type": "string"}},
                     "constraints": "candidate is an input candidate ID satisfying maximum_price and required capabilities. Explanation and comparative value require judgment."},
        "briefing": {"required": ["text", "sources"], "properties": {"text": {"type": "string", "minLength": 31}, "sources": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
                     "constraints": "Top-level text contains the briefing. Top-level sources contains source IDs from the task, not prose names. Passing format remains unassessed: truth, usefulness and fit require independent judgment, not another automatic run."},
    }
    if kind not in specs:
        return {"coverage": "undocumented", "constraints": "No public receiving contract registered; do not infer universal support or failure from this adapter."}
    return {"type": "object", "coverage": "narrow receiving workflow", **specs[kind]}

BUYERS = [
    ("northstar", "Northstar Studio", "Deliver client websites while keeping maintenance commitments", "records", 36,
     "Existing templates; client approvals take time. Prefer integration over replacing the stack."),
    ("juniper", "Juniper Goods", "Grow a useful digital-products shop and retain customers", "records", 36,
     "Thin margins, an adequate storefront, and complaints about copying purchased material."),
    ("relay", "Relay Software", "Operate and improve a dependable subscription application", "schedule", 60,
     "Release and support work compete. A missed maintenance window has consequences."),
    ("common", "Common Ground", "Keep a membership publication useful and worth returning to", "briefing", 24,
     "Readers dislike generic summaries. Preserve editorial voice and source uncertainty."),
    ("ledgerbird", "Ledgerbird Agency", "Deliver trustworthy recurring reports to several clients", "records", 48,
     "Client definitions differ. Existing spreadsheet exports work but take manual effort."),
    ("waypoint", "Waypoint Learning", "Help learners make progress using a changing course library", "briefing", 36,
     "Accessible explanations and meaningful learning matter more than volume of content."),
    ("kite", "Kite Collective", "Coordinate contributors and events without exhausting volunteers", "schedule", 24,
     "A small budget and a workable calendar. Simplicity often beats a new subscription."),
    ("meridian", "Meridian Procurement", "Find suitable tools for client projects and maintain their trust", "sourcing", 24,
     "Shortlists must respect client constraints. Sponsored visibility is not endorsement."),
]

SUBJECTS = {
    "reach": "Build a valuable company helping useful offerings reach and serve suitable customers, including agent-mediated buyers.",
    "steward": "Build a valuable company removing recurring burdens from people's work and operations.",
    "frontier": "Discover a worthwhile unmet need and develop an offering people choose to use repeatedly.",
}

# Small representative panel, never multiplied into fictional customer counts.
CONSUMERS = {
    "maya": ("Maya", 8, "Find enjoyable things to do in short breaks and develop a creative hobby", "I have little free time; quick first value matters. Generic repeats bore me. I might pay a little for reliably good personal suggestions."),
    "eli": ("Eli", 2, "Enjoy quiet leisure and discover interesting things without spending much", "Free alternatives are usually enough. Prefer readable, accessible activities; no required video, social posting or elaborate onboarding."),
    "nia": ("Nia", 6, "Make family downtime enjoyable without planning becoming another chore", "Simple shared activities and clear preparation matter. Some evenings are interrupted. A subscription must earn its place; I can stop using or cancel it."),
    "theo": ("Theo", 14, "Explore deeper creative interests and find experiences worth returning for", "I will pay for distinctive depth, not generic lists. Privacy and an easy exit matter. Familiar suggestions can be a reason to switch, not buy again."),
}


def actors(pack):
    if pack in PACKS:
        return PACKS[pack]()
    if pack == "market":
        result = {a: {"name": name, "role": "counterpart", "purpose": purpose, "preferences": preference,
                      "adapter": adapter, "endowment": money, "review_seconds": 1800}
                  for a, name, purpose, adapter, money, preference in BUYERS}
        result.update({a: {"name": a.title(), "role": "subject", "purpose": goal, "endowment": 12} for a, goal in SUBJECTS.items()})
        return result
    if pack == "consumer":
        result = {a: {"name": name, "role": "counterpart", "customer_type": "individual_consumer",
                      "purpose": purpose, "preferences": preferences, "adapter": "leisure",
                      "endowment": money, "review_seconds": 1800,
                      "resource_meaning": "Project staff_remaining is personal attention/effort, not employees or business capacity. No income is earned by using a service."}
                  for a, (name, money, purpose, preferences) in CONSUMERS.items()}
        result["everyday"] = {"name": "Everyday", "role": "subject", "endowment": 12,
            "purpose": "Build a worthwhile consumer service people freely choose, enjoy and return to in their personal lives. Discover what is valuable; you own the offering, methods and business model."}
        return result
    if pack == "research":
        return {"researcher": {"name": "Research undertaking", "role": "subject", "purpose": "Maintain a useful, source-grounded understanding of an evolving topic and help collaborators decide what to do."},
                "collaborator": {"name": "Research collaborator", "role": "counterpart", "purpose": "Use changing evidence to decide which study to pursue", "adapter": "briefing", "preferences": "Separate findings, uncertainty and speculation; retain contradictory evidence.", "review_seconds": 1800}}
    if pack == "coordination":
        return {"coordinator": {"name": "Coordination undertaking", "role": "subject", "purpose": "Help a group sustain its commitments as availability and priorities change."},
                "organizer": {"name": "Volunteer organizer", "role": "counterpart", "purpose": "Arrange workable sessions without scheduling people beyond their availability", "adapter": "schedule", "preferences": "Keep existing arrangements when they work. Ask about missing constraints.", "review_seconds": 1800}}
    raise ValueError("unknown scenario pack")


def workload(adapter, seed, period=0):
    if adapter in ADAPTERS:
        return ADAPTERS[adapter][0](seed, period)
    rng = random.Random(f"{seed}:{adapter}:{period}")
    if adapter == "leisure":
        person = str(seed).split(":")[-1]
        minutes, formats = {"maya": (15, ["text", "audio"]), "eli": (30, ["text"]),
                            "nia": (20, ["text", "shared"]), "theo": (45, ["text", "audio"])}.get(person, (20, ["text"]))
        available = max(10, minutes-rng.choice([0, 0, 5]))
        themes = rng.sample(["drawing", "music", "stories", "nature", "puzzles", "making"], 4)
        return {"adapter": "leisure", "occasion": f"Personal downtime, period {period}",
                "available_minutes": available, "maximum_cost": 0 if person == "eli" else 2,
                "allowed_formats": formats,
                "catalog": [{"id": f"p{period}-activity-{i}", "theme": theme, "minutes": 10+i*5,
                             "cost": 0 if i < 2 else 2, "format": "text" if i < 3 else "audio",
                             "description": f"A self-contained {theme} activity; no account, equipment purchase or public sharing required."}
                            for i, theme in enumerate(themes)],
                "task": "Suggest a personally worthwhile experience for this occasion, with usable guidance. The catalog is a free alternative, not the only permissible product format. Catalog feasibility is only a basic check; novel or older activities need independent assessment against my current constraints. Whether I enjoy it or return is my judgment. Free self-selection remains available."}
    if adapter == "records":
        records = [{"id": f"item-{i}", "amount": rng.randrange(2, 25), "label": f"Material {i}", "attachment": f"asset-{i}.txt"} for i in range(3+period % 3)]
        return {"adapter": adapter, "records": records, "required": ["id", "amount", "label", "attachment"],
                "format": "json", "task": "Produce an importable JSON object with records preserved and total_amount calculated. Do not discard attachments."}
    if adapter == "schedule":
        jobs = [{"id": f"session-{i}", "allowed": sorted(rng.sample(list(range(6)), 3))} for i in range(3)]
        # Keep an available known assignment; arbitrary random constraints must not make the world impossible.
        for i, job in enumerate(jobs):
            job["allowed"] = sorted(set(job["allowed"]+[i]))
        return {"adapter": adapter, "jobs": jobs, "task": "Assign every session one allowed slot, with no two sessions using the same slot."}
    if adapter == "sourcing":
        return {"adapter": adapter, "maximum_price": 8+period % 3, "required": ["export", "offline"],
                "candidates": [{"id": "plain", "price": 6, "capabilities": ["export", "offline"], "sponsored": False},
                               {"id": "famous", "price": 20, "capabilities": ["export"], "sponsored": True},
                               {"id": "niche", "price": 8, "capabilities": ["export", "offline", "sync"], "sponsored": False}],
                "task": "Recommend a feasible candidate and explain the fit. Sponsored placement is not evidence of suitability."}
    return {"adapter": "briefing", "topic": "Choosing a method for the next project", "revision": period,
            "sources": [{"id": "s1", "text": "Method A worked on small samples; large-sample performance has not been measured."},
                        {"id": "s2", "text": "Method B worked on large samples but took more preparation."},
                        {"id": "s3", "text": "Our next project uses a large sample and allows preparation time."}],
            "task": "Write a useful concise recommendation with source references and uncertainty. Do not invent research or confuse small- and large-sample evidence."}


def baseline(task):
    kind = task["adapter"]
    if kind in ADAPTERS:
        return ADAPTERS[kind][1](task)
    if kind == "leisure":
        a = next(a for a in task["catalog"] if a["minutes"] <= task["available_minutes"] and a["cost"] <= task["maximum_cost"] and a["format"] in task["allowed_formats"])
        return {"activity": a["id"], "text": f"Try the {a['theme']} activity for {a['minutes']} minutes. Choose one small thing to explore, spend the time on it, and decide whether you enjoyed it. This is a generic free suggestion, not personalized guidance."}
    if kind == "records":
        return {"records": task["records"], "total_amount": sum(r["amount"] for r in task["records"])}
    if kind == "schedule":
        return {"assignments": {j["id"]: i for i, j in enumerate(task["jobs"])}}
    if kind == "sourcing":
        possible = [c for c in task["candidates"] if c["price"] <= task["maximum_price"] and set(task["required"]) <= set(c["capabilities"])]
        return {"candidate": min(possible, key=lambda c: c["price"])["id"], "reason": "Meets required capabilities within budget."}
    return {"text": "Consider Method B for this large-sample project: s2 reports relevant results and s3 permits preparation. Method A's large-sample performance remains unknown (s1).", "sources": ["s1", "s2", "s3"]}


def consume(task, output):
    """Execute narrow receiving contracts; subjective quality remains unassessed."""
    if not isinstance(output, dict):
        return {"status": "failed", "reason": "Receiving workflow requires an object", "attribution": "delivery", "semantic": "unassessed"}
    kind = task.get("adapter")
    if kind in ADAPTERS:
        return ADAPTERS[kind][2](task, output)
    if kind == "leisure":
        a = next((a for a in task["catalog"] if a["id"] == output.get("activity")), None)
        if a is None:
            return {"status": "unassessed", "reason": "Product is outside the current catalog adapter; missing or changed catalog identity does not establish infeasibility",
                    "attribution": "adapter_coverage", "judgment_required": True,
                    "semantic": "Current feasibility, actual usefulness, enjoyment and retention require independent assessment; no automatic acceptance or payment"}
        good = bool(a and a["minutes"] <= task["available_minutes"] and a["cost"] <= task["maximum_cost"] and a["format"] in task["allowed_formats"] and isinstance(output.get("text"), str) and len(output["text"]) > 30)
        return {"status": "passed" if good else "failed", "reason": "Experience fits explicit occasion constraints" if good else "activity must be a current feasible catalog ID and text a usable string of at least 31 characters", "attribution": "receiving_workflow", "semantic": "Feasibility only; enjoyment, personal value and retention remain unassessed", "judgment_required": bool(good)}
    if kind == "records":
        missing = [key for key in ("records", "total_amount") if key not in output]
        if missing:
            return {"status": "failed", "reason": "Receiving payload missing root fields: " + ", ".join(missing),
                    "attribution": "receiving_workflow", "semantic": "Submitted JSON body checked directly; embedded source code was not executed. This is a receiving-shape mismatch, not evidence that the embedded program loses data."}
        good = output.get("records") == task["records"] and output.get("total_amount") == sum(r["amount"] for r in task["records"])
        return {"status": "passed" if good else "failed", "reason": "Record import, attachments and total preserved" if good else "Submitted records, attachments, or total differ from the task", "attribution": "receiving_workflow", "semantic": "Submitted JSON body checked directly; embedded source code was not executed. Narrow receiving contract only."}
    if kind == "schedule":
        a = output.get("assignments", {})
        good = isinstance(a, dict) and set(a) == {j["id"] for j in task["jobs"]}
        if good:
            good = all(type(a[j["id"]]) is int and a[j["id"]] in j["allowed"] for j in task["jobs"]) and len(set(a.values())) == len(a)
        return {"status": "passed" if good else "failed", "reason": "Assignments feasible" if good else "Missing, conflicting, or unavailable assignment", "attribution": "receiving_workflow", "semantic": "narrow executed contract"}
    if kind == "sourcing":
        match = next((c for c in task["candidates"] if c["id"] == output.get("candidate")), None)
        good = bool(match and match["price"] <= task["maximum_price"] and set(task["required"]) <= set(match["capabilities"]))
        return {"status": "passed" if good else "failed", "reason": "Selected option meets explicit constraints" if good else "Recommendation violates client constraints", "attribution": "receiving_workflow", "semantic": "Comparative value and explanation unassessed"}
    if kind == "briefing":
        refs = output.get("sources", [])
        errors = []
        if not isinstance(output.get("text"), str) or len(output["text"]) <= 30:
            errors.append("text: required top-level string of at least 31 characters")
        if not isinstance(refs, list) or not refs or not all(isinstance(x, str) and x in {s["id"] for s in task["sources"]} for x in refs):
            errors.append("sources: required nonempty top-level array of source IDs present in task.sources")
        return {"status": "failed" if errors else "unassessed", "reason": "; ".join(errors) if errors else "Readable sourced briefing; judgment required", "attribution": "receiving_workflow", "semantic": "Truth, usefulness and preference fit require independent judgment"}
    return {"status": "unassessed", "reason": "No registered outcome adapter", "attribution": "adapter_coverage", "semantic": "unassessed"}


def circumstance(seed, actor, period):
    """Exogenous draws do not inspect seller success or consume a shared RNG stream."""
    if actor in ("kite", "organizer"):
        return "routine"  # Stable-operation control.
    if actor in CONSUMERS:
        # Realizable new occasions, not automatic new purchase demand. Actors
        # retain earlier tastes/memories and choose whether to use or pay again.
        return "adjacent_project"
    x = random.Random(f"world-v1:{seed}:{actor}:{period}").random()
    if x < .70:
        return "routine"
    if x < .90:
        return random.Random(f"adjacent:{seed}:{actor}:{period}").choice(["adjacent_project", "new_contact", "preference_shift"])
    return random.Random(f"disruption:{seed}:{actor}:{period}").choice(["capacity_loss", "budget_pause", "source_correction"])


def shared_circumstance(seed, period):
    """One external cause can affect several compatible dependencies, not all lives."""
    if period > 0 and random.Random(f"shared-format:{seed}:{period}").random() < .15:
        return {"change": "receiving_format_revision", "adapter": "records", "required_field": "locale",
                "reason": "The shared publishing integration now preserves locale in imported records"}
    return None


def adjacent_workload(task, seed, period):
    kind = {"records": "briefing", "briefing": "sourcing", "sourcing": "schedule", "schedule": "records"}.get(task["adapter"], task["adapter"])
    return workload(kind, seed, period)

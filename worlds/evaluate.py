"""Optional blind qualitative review. Advisory only; cannot act for a participant."""
import json
from .observe import review_packet
from .store import encoded, require


def evaluate(world, observation, model):
    packet = review_packet(world, observation)
    with world.s.transaction() as db:
        owner = world.s.get(db, observation)["owner"]
    prompt = """You are an independent reviewer, not a buyer or seller. Review only the
provided brief, output, and mechanical evidence. Preserve uncertainty and cite
specific supporting or contradicting details. Do not infer value from style,
length, claimed success, or source-link presence alone. Return actions=[], finish=true; put a
JSON object in note with dimensions (a list of judgments with evidence), unknowns
(a list), and attribution (delivery, receiving_workflow, adapter_coverage, or unclear).
No score, commercial action, or private seller reasoning is available.
"""+encoded(packet)
    result = model(owner, prompt, category="evaluation")
    require(result.get("actions") == [], "reviewer cannot issue actions")
    judgment = json.loads(result["note"])
    require(isinstance(judgment.get("dimensions"), list) and isinstance(judgment.get("unknowns"), list), "review must retain dimensions and uncertainty")
    with world.s.transaction() as db:
        record = world.s.put(db, "evaluation", "_observer", {"observation": observation, "judgment": judgment,
            "basis": "independent model assessment; not world truth or buyer preference", "packet": packet})
        world.s.event(db, "_observer", "semantic_review", {"evaluation": record["id"]})
        return record

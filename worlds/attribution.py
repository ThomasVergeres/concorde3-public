"""Bounded reviewer-only provenance paths. Citations are not causal proof."""
import json


def evidence(db, actors, limit=8, depth=4, since=None, until=None):
    if not 1 <= limit <= 32 or not 1 <= depth <= 8:
        raise ValueError("bounded attribution request required")
    records = {}
    for identity, kind, owner, audience, body in db.execute(
            "SELECT id,kind,owner,audience,body FROM records WHERE kind IN ('artifact','message','observation')"):
        records[identity] = {"id":identity,"kind":kind,"owner":owner,"audience":json.loads(audience),**json.loads(body)}

    def traces(identity, actor, path=(), budget=None):
        if budget is None: budget=[256]
        if budget[0]<=0: return [], ["lineage traversal budget exhausted"]
        budget[0]-=1
        if identity in path: return [], ["citation cycle"]
        if len(path) >= depth: return [], ["lineage depth omitted"]
        item = records.get(identity)
        if not item: return [], ["unresolved source reference"]
        path = (*path, identity)
        found, gaps = [], []
        if item.get("producer",item["owner"]) == actor:
            found.append(list(path))
        for ref in item.get("source_refs",[])[:32]:
            child, missing = traces(ref,actor,path,budget)
            found.extend(child); gaps.extend(missing)
            if len(found)>=32: gaps.append("lineage breadth omitted"); break
        if len(item.get("source_refs",[]))>32: gaps.append("source references omitted")
        return found[:32], gaps

    result = {}
    for actor in actors:
        uses, gaps = [], []
        for o in records.values():
            if o["kind"] != "observation" or o["owner"] == actor or not o.get("artifact"): continue
            if since is not None or until is not None:
                at = o.get("at")
                if not isinstance(at, (int, float)):
                    gaps.append("undated receiving observation omitted")
                    continue
                if (since is not None and at < since) or (until is not None and at > until):
                    continue
            paths, missing = traces(o["artifact"],actor)
            gaps.extend(missing)
            if not paths: continue
            # Observation visibility is separate from evaluator access. We do not
            # infer an exposure event or comprehension from record accessibility.
            visible = actor in o["audience"] or "*" in o["audience"]
            uses.append({"observation":o["id"],"at":o.get("at"),"consumer":o["owner"],"artifact":o["artifact"],
                "outcome":o.get("outcome"),"project":o.get("project"),"work_id":o.get("work_id"),
                "source_paths":paths,"attribution":"direct producer" if any(len(p)==1 for p in paths) else "declared citation chain",
                "observation_visible_to_subject":visible,
                "exposure":"accessibility only; served/read evidence must be checked separately"})
        result[actor] = {"observations":uses[-limit:],"matched_observations":len(uses),"omitted":max(0,len(uses)-limit),
            "unique_consumers":len({o["consumer"] for o in uses}),
            "lineage_limitations":sorted(set(gaps)),
            "basis":"Observed receiving use plus producer/citation provenance. Not proof of causal contribution, satisfaction, independent users across contributors, payment or external demand. Failed outcomes remain failures."}
    return result

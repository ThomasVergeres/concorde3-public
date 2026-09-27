"""Recorded operator calibration of counterpart response capacity, not C3 attention."""
import argparse
import json
from pathlib import Path
import time

from evals.lab import save
from worlds.engine import World


def adjust(world, limit, reserve, reason, *, apply=False):
    if type(limit) is not int or type(reserve) is not int or not 1 <= limit <= 32 or not 0 <= reserve < limit:
        raise ValueError("bounded counterpart allowance/reserve required")
    if not isinstance(reason, str) or not reason.strip(): raise ValueError("operator reason required")
    with world.s.transaction() as db:
        world.s.alive(db)
        cfg = world.s.meta(db, "config")
        if limit < cfg.get("counterpart_calls_per_hour", 8): raise ValueError("this calibration only raises capacity")
        before = {key: cfg.get(key) for key in ("counterpart_calls_per_hour", "counterpart_response_reserve")}
        after = {"counterpart_calls_per_hour": limit, "counterpart_response_reserve": reserve}
        now = world.s.clock(); retries = []
        for row in db.execute("SELECT id,body FROM actors"):
            actor, info = row["id"], json.loads(row["body"])
            if info["role"] != "counterpart": continue
            c = world.s.get(db, "continuation:"+actor)
            last = db.execute("SELECT body FROM events WHERE kind='counterpart_episode_failure' AND json_extract(body,'$.actor')=? ORDER BY seq DESC LIMIT 1", (actor,)).fetchone()
            error = json.loads(last[0]).get("error", "") if last else ""
            count = db.execute("SELECT count(*) FROM calls WHERE actor=? AND category='counterpart' AND at>? AND status!='undispatched'", (actor, now-3600)).fetchone()[0]
            capacity = world.counterpart_capacity(db, actor)
            if (c.get("retry_after", 0) > now and c.get("reason") == "failed episode backoff; inbox cannot bypass resource limits"
                    and "counterpart hourly budget" in error and count < limit
                    and (capacity["responding"] or count < limit-reserve)):
                retries.append({"actor": actor, "previous_retry_after": c["retry_after"], "calls_this_hour": count})
                if apply:
                    world.s.revise(db, c, retry_after=now, next_at=min(c["next_at"], now),
                        reason="Operator response-capacity calibration released documented budget backoff; actor choices unchanged")
        result = {"world": str(world.s.root), "at": now, "applied": apply, "before": before,
            "after": after, "released_budget_backoffs": retries, "reason": reason,
            "unchanged": ["subject model/attention/start cap", "world and shared call ceilings", "cutoff", "customer purchase decisions"]}
        if apply:
            world.s.meta(db, "config", {**cfg, **after})
            result["event"] = world.s.event(db, "_operator", "response_capacity_calibration", result)
        return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("root", type=Path)
    p.add_argument("--limit", type=int, required=True); p.add_argument("--reserve", type=int, required=True)
    p.add_argument("--reason", required=True); p.add_argument("--apply", action="store_true")
    a = p.parse_args(); manifest = json.loads((a.root/"cohort.json").read_text())
    results = [adjust(World(Path(path)), a.limit, a.reserve, a.reason, apply=a.apply)
               for path in manifest["worlds"].values()]
    if a.apply: save(a.root/"operator-interventions"/f"response-capacity-{time.time_ns()}.json", results)
    print(json.dumps(results, indent=2))

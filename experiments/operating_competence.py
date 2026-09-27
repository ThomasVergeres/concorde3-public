"""Prepare the finite, uncoached Terra transfer lives for operating competence.

No custom customer choices, seeds about desired cognitive behavior, or judgments.
Uses existing world packs and bounded counterpart allowances. Historical v1
reserved six of eight calls for message responses; delivered artifacts alone did
not reliably admit follow-up inspection. This is a reproduction configuration,
not a qualified adoption benchmark. See the operating-competence results.
"""
import argparse
import json
from pathlib import Path
import time

from evals.lab import command, save
from worlds.engine import World
from worlds.cohort import reference_controls


def prepare(root, image, minutes=45, packs=("market", "consumer"), seed=41, response_reserve=6):
    if not 30 <= minutes <= 60:
        raise ValueError("finite 30–60 minute transfer window required")
    if not packs or len(set(packs)) != len(packs) or any(pack not in ("market", "consumer") for pack in packs):
        raise ValueError("select market and/or consumer once each")
    if type(seed) is not int or seed < 1:
        raise ValueError("positive independent world seed required")
    if type(response_reserve) is not int or not 0 <= response_reserve < 8:
        raise ValueError("response reserve must be an integer from zero through seven")
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("new experiment directory required")
    root.mkdir(parents=True, mode=0o700)
    started = time.time()
    manifest = {"started": started, "cutoff": started + minutes * 60,
                "status": "prepared", "worlds": {},
                "image": command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]),
                "source_revision": command(["git", "rev-parse", "HEAD"]),
                "shared_calls_per_hour": 200, "shared_concurrency": 8,
                "purpose": "Terra-medium open-ended operating transfer; independent simulated choices, no automatic competence verdict",
                "profile": "operating-competence"}
    for pack, cap, concurrency in (("market", 110, 4), ("consumer", 60, 2)):
        if pack not in packs: continue
        name = pack + "-" + str(seed)
        world = World(root / name)
        world.create(pack, seed, minutes / 60)
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg.update(started=started, cutoff=manifest["cutoff"],
                       model="gpt-5.6-terra", effort="medium",
                       calls_per_hour=cap, concurrency=concurrency, shared_concurrency=8,
                       counterpart_calls_per_hour=8, counterpart_response_reserve=response_reserve,
                       baseline_starts=6, maximum_starts=12, activation_deadline=300,
                       period_seconds=600, staff_per_period=24,
                       experiment="operating-competence-transfer-v1")
            world.s.meta(db, "config", cfg)
            for index, project in enumerate(world.s.rows(db, "project")):
                world.s.revise(db, project, staff_remaining=24)
                continuation = world.s.get(db, "continuation:" + project["owner"])
                world.s.revise(db, continuation, next_at=started + 45 + index * 20,
                               reason="Declared ordinary counterpart review; enough response capacity retained")
            world.s.event(db, "_operator", "protocol", {
                "config": cfg, "reason": "Fresh open-ended transfer, not private reentry coaching or forced customer adoption"})
        if pack == "market":
            reference_controls(world)
        manifest["worlds"][name] = str(world.s.root)
    save(root / "cohort.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--minutes", type=int, default=45)
    parser.add_argument("--packs",default="market,consumer")
    parser.add_argument("--seed",type=int,default=41)
    parser.add_argument("--response-reserve",type=int,default=6,
                        help="Reserved correspondence calls within the unchanged eight-call cap; use 2 for broader project review")
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.image, args.minutes, tuple(args.packs.split(",")), args.seed, args.response_reserve), indent=2))

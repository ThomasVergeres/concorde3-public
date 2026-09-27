"""Mechanical controls and optional live counterpart calibration. Never seller success."""
import json
from pathlib import Path
import tempfile
from .engine import World
from . import scenarios
from .observe import report


def fixtures(world, buyer="northstar"):
    project = world.view(buyer, "project")["records"][0]
    offers = []
    for actor, title, content, price in (
        ("reach", "Compact importer", scenarios.baseline(project["task"]), 2),
        ("steward", "Premium project accelerator", {"message": "Your work is perfectly completed!"}, 5),
        ("frontier", "Manual export copy", scenarios.baseline(project["task"]), 8)):
        artifact = world.act(actor, "fixture-artifact", {"op": "artifact", "title": title, "content": content, "audience": [buyer]})["result"]
        offer = world.act(actor, "fixture-offer", {"op": "offer", "title": title, "price": price, "terms": "One prepared output for the current import. Inspect the free sample before deciding. No future compatibility promise.", "delivery": artifact["id"]})["result"]
        offers.append({"actor": actor, "offer": offer["id"], "artifact": artifact["id"]})
    return project, offers


def qualify(output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for seed in (1, 2, 3):
        for pack in ("market", "research", "coordination"):
            import time
            clock = [time.time()]
            world = World(root/f"{pack}-{seed}", lambda: clock[0])
            world.create(pack, seed)
            world.tick()
            result = {"pack": pack, "seed": seed, "basis": "mechanical fixture, no model behavior"}
            if pack == "market":
                project, offers = fixtures(world)
                statuses = []
                for index, offer in enumerate(offers):
                    observation = world.act("northstar", f"consume-{index}", {"op": "consume", "project": project["id"], "artifact": offer["artifact"]})["result"]
                    statuses.append(observation["outcome"]["status"])
                assert statuses == ["passed", "failed", "passed"]
                checkout = world.act("northstar", "buy-reference", {"op": "checkout", "offer": offers[0]["offer"], "agreed_price": 2})["result"]
                world.act("northstar", "adopt-reference", {"op": "approach", "project": project["id"], "method": "artifact", "artifact": offers[0]["artifact"], "reason": "Fixture exercises repeat operation"})
                for _ in range(2):
                    clock[0] += 600
                    world.tick()
                assert report(world)["actors"]["reach"]["delivered_use"]["distinct_successful_work"] == 3
                result.update(compatibility=statuses, paid=checkout["captured"])
            world.freeze()
            result["integrity"] = world.s.verify()
            result["report"] = report(world)
            results.append(result)
    return results


def live(output, seeds=(1, 2, 3), image="concorde3:lab-current"):
    from .driver import SubscriptionDriver, episode
    results = []
    for seed in seeds:
        world = World(Path(output)/f"live-{seed}")
        world.create(seed=seed, hours=1)
        world.tick()
        project, offers = fixtures(world)
        try:
            receipts = episode(world, "northstar", SubscriptionDriver(world, image))
            results.append({"seed": seed, "receipts": receipts, "report": report(world), "limitation": "One short buyer episode, not a functioning company or market-demand result"})
        finally:
            world.freeze()
    return results

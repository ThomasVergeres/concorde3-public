"""Run, inspect and qualify generic worlds with optional scenario modules."""
import argparse
import json
from pathlib import Path
import time
from .engine import World
from .observe import report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("init", "tick", "observe", "serve", "qualify", "live-qualify", "run", "attach", "freeze"))
    p.add_argument("root", type=lambda x: Path(x).resolve())
    p.add_argument("--pack", choices=("market", "research", "coordination", "consumer"), default="market")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--image", default="concorde3:lab-current")
    p.add_argument("--subjects", nargs="+", help="run only these subject IDs; excluded copied selves must already be frozen")
    args = p.parse_args()
    if args.subjects is not None and args.action != "run":
        p.error("--subjects is only valid for a new run, not attach or other actions")
    world = World(args.root)
    if args.action == "init":
        result = world.create(args.pack, args.seed, args.hours)
    elif args.action == "tick": result = world.tick()
    elif args.action == "observe": result = report(world)
    elif args.action == "serve":
        from .server import server
        httpd = server(world, args.host, args.port)
        httpd.timeout = 1
        while True:
            with world.s.transaction() as db:
                if world.s.meta(db, "frozen") or world.s.clock() >= world.s.meta(db, "config")["cutoff"]:
                    break
            httpd.handle_request()
        httpd.server_close()
        return
    elif args.action == "qualify":
        from .qualify import qualify
        result = qualify(args.root)
    elif args.action == "live-qualify":
        from .qualify import live
        result = live(args.root, image=args.image)
    elif args.action in ("run", "attach"):
        from .campaign import run
        run(world, args.image, attach=args.action == "attach", subjects=args.subjects if args.subjects is not None else True)
        return
    else:
        from .campaign import freeze
        freeze(world)
        result = report(world)
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()

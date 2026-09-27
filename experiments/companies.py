#!/usr/bin/env python3
"""Finite isolated company ecology. Experiment adapters only; no C3 core edits."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import random
import secrets
import signal
import sqlite3
import subprocess
import time
from worlds.auth import subscription_auth_file

if __package__:
    from .company_seeds import ALL, VENTURES, CUSTOMERS, BOUNDARY, CAPABILITY, world, SHOCKS
else:
    from company_seeds import ALL, VENTURES, CUSTOMERS, BOUNDARY, CAPABILITY, world, SHOCKS

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin/concorde3"
PROFILE = "classic"


def set_profile(profile):
    global PROFILE, ALL, VENTURES, CUSTOMERS, BOUNDARY, CAPABILITY, world, SHOCKS
    import importlib
    module_name = "field_seeds" if profile == "field" else "company_seeds"
    module = importlib.import_module((__package__ + "." if __package__ else "") + module_name)
    PROFILE = profile
    for key in ("ALL", "VENTURES", "CUSTOMERS", "BOUNDARY", "CAPABILITY", "world", "SHOCKS"):
        globals()[key] = getattr(module, key)


def run(*args, timeout=35, **kwargs):
    return subprocess.run(list(map(str, args)), text=True, capture_output=True,
                          check=True, timeout=timeout, **kwargs).stdout.strip()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value, indent=2))
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def read(path):
    return json.loads(path.read_text())


def log(root, kind, **data):
    with (root / "running-log.jsonl").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, **data}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def prepare(root, hours, image):
    if root.exists():
        raise RuntimeError("refusing to overwrite an existing experiment")
    if not 0 < hours <= 24:
        raise RuntimeError("finite horizon must be >0 and <=24 hours")
    os.umask(0o077)
    root.mkdir(parents=True)
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:10]
    m = {"prepared": time.time(), "hours": hours, "revision": run("git", "rev-parse", "HEAD", cwd=REPO),
         "image_id": run("docker", "image", "inspect", "--format", "{{.Id}}", image),
         "prefix": "c3-company-" + suffix, "model": "gpt-5.6-terra", "effort": "medium",
         "baseline_starts": 4, "boosted_starts": 8, "customer_starts": 6,
         "max_cohort_starts_per_hour": 44, "deadline_seconds": 600,
         "auth_basis": "ChatGPT subscription only", "containers": {n: "c3-company-" + suffix + "-" + n for n in ALL},
         "sources": {}, "status": "prepared", "profile": PROFILE,
         "currency_supply": 120 + 6 * len(VENTURES) + 48 * len(CUSTOMERS)}
    for file in ("companies.py", "company_market.py", "company_seeds.py", "company_subject.py", "company_client.py", "company-market-contract.md",
                 "field_seeds.py", "field_business.py", "field_consumers.py", "field-market-contract.md", "public_research.py", "research_client.py"):
        source = (REPO / "experiments" / file).read_text()
        save(root / "assets" / file, source)
        m["sources"][file] = hashlib.sha256(source.encode()).hexdigest()
    transport = (REPO / "evals/transport.py").read_text()
    save(root / "assets/transport.py", transport)
    m["sources"]["transport.py"] = hashlib.sha256(transport.encode()).hexdigest()
    market = {"actors": {}, "balances": {"treasury": 120, "escrow": 0, "capacity": 0}, "allow_customer_boost": PROFILE == "field"}
    for name, (title, goal) in ALL.items():
        instance = root / "instances" / name
        run(BIN, "init", "--workspace", "--goal", f"You are {title}. {goal}", instance)
        st = read(instance / ".concorde2/state.json")
        items = [{"expected_revision": 0, "item": {"id": "experiment-boundary", "node": "experiment", "kind": "norm", "status": "active", "text": BOUNDARY}},
                 {"expected_revision": 0, "item": {"id": "market-capability", "node": "experiment", "kind": "capability", "status": "active", "text": CAPABILITY}}]
        run(BIN, "call", instance, "mutate", json.dumps({"reason": "operator seeds experiment authority and available commercial surface, not business strategy", "changes": {
            "expected_seq": st["sequence"] if "sequence" in st else st["seq"],
            "nodes": [{"expected_revision": 0, "node": {"id": "experiment", "title": "Experimental setting and market capability", "status": "active"}}], "items": items}}))
        cap = 4 if name in VENTURES else 6
        run(BIN, "configure", instance, json.dumps({"starts_per_hour": cap, "deadline_seconds": 600,
            "external_sandbox": True, "model": m["model"], "effort": m["effort"],
            "global": ["purpose", "capabilities", "experiment-boundary", "market-capability"]}))
        actor = {"role": "venture" if name in VENTURES else "customer", "description": title + ": " + goal,
                 "token": secrets.token_urlsafe(32), "product_host": "unset", "boosted_starts": 10 if PROFILE == "field" and name in CUSTOMERS else 8}
        market["actors"][name] = actor
        market["balances"][name] = 6 if name in VENTURES else 48
        for filename, value in world(name).items():
            save(root / "exchange" / name / filename, value)
        save(root / "access" / name / "access.json", {"token": actor["token"], "url": "unset"})
        contract = "field-market-contract.md" if PROFILE == "field" else "company-market-contract.md"
        save(root / "access" / name / "README.md", (root / "assets" / contract).read_text())
        save(root / "access" / name / "client.py", (root / "assets/company_client.py").read_text())
        if PROFILE == "field":
            save(root / "access" / name / "research.py", (root / "assets/research_client.py").read_text())
        if PROFILE == "field" and name in CUSTOMERS:
            seed_operating_business(root, name)
    save(root / "market/config.json", market)
    save(root / "manifest.json", m)
    log(root, "prepared", source_hashes=m["sources"], max_starts_per_hour=44)


def seed_operating_business(root, name):
    instance = root / "instances" / name
    save(instance / "business.py", (root / "assets/field_business.py").read_text())
    if name == "beacon":
        save(instance / "publication.json", {"title": "Beacon", "articles": [
            {"id": "welcome", "title": "Welcome to Beacon", "summary": "Practical changes for small software businesses.", "topics": ["business"]}]})
    else:
        save(instance / "catalog.json", {"products": [
            {"sku": "launch-checklist", "name": "Service launch checklist", "price_units": 5, "content": "# Launch checklist\n- Define the customer promise.\n- Exercise a real request.\n- Check failure handling.\n- Verify backups and rollback.\n- Publish usage and support instructions.\n"},
            {"sku": "onboarding-pack", "name": "Customer onboarding checklist", "price_units": 7, "content": "# Customer onboarding\n- Capture the desired outcome.\n- Confirm required access.\n- Deliver the first useful result.\n- Explain limitations and support.\n- Schedule outcome review.\n"}]})
    note = """You inherited a running service: /instance/business.py, registered as
business-service under purpose. Own, extend or replace it. Beacon's /publication
returns {articles:[{id,title,summary,topics,source_url,source_retrieved_at}]} from
publication.json; Parcel's /catalog and /delivery/ORDER_ID read catalog.json and
/exchange/orders.json. Consumer calls use these public contracts, not graph IDs.
You may add products/interfaces; preserve existing customers or provide migration.
Use /exchange/customer-feedback.json for consumer attempts, not inferred sales.
Your private files and interfaces are an existing business, not a required roadmap."""
    run(BIN, "call", instance, "mutate", json.dumps({"reason": "inherit an already operating company and editable assets", "changes": {
        "items": [{"expected_revision": 0, "item": {"id": "inherited-operation", "node": "experiment", "kind": "capability", "status": "active", "text": note}}],
        "programs": [{"id": "business-service", "command": ["python3", "/instance/business.py", name], "intention": "purpose", "enabled": True}]}}))
    run(BIN, "configure", instance, json.dumps({"global": ["purpose", "capabilities", "experiment-boundary", "market-capability", "inherited-operation"]}))


def create_network(name):
    routes = json.loads(run("ip", "-j", "-4", "route"))
    used = [ipaddress.ip_network(r["dst"], strict=False) for r in routes if r.get("dst") not in (None, "default")]
    for _ in range(64):
        slot = random.SystemRandom().randrange(8192)
        subnet = ipaddress.ip_network((int(ipaddress.ip_address("10.244.0.0")) + slot * 8, 29))
        if any(subnet.overlaps(n) for n in used):
            continue
        try:
            run("docker", "network", "create", "--internal", "--subnet", subnet, name)
            return {"network": name, "market": str(subnet[2]), "proxy": str(subnet[3]), "company": str(subnet[4])}
        except subprocess.CalledProcessError as error:
            if "overlap" not in error.stderr.lower():
                raise
    raise RuntimeError("no free isolated subnet")


def restrictions(memory="2g", cpus="1"):
    return ["--restart=no", "--memory", memory, "--cpus", cpus, "--pids-limit", "256", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,size=256m,mode=1777"]


def launch(root, qualification=False):
    m = read(root / "manifest.json")
    if m["status"] != "prepared":
        raise RuntimeError("refusing implicit restart")
    # Refuse overlap rather than stop unrelated/newly started work.
    live = run("docker", "ps", "--format", "{{.Names}}")
    if any(n.startswith("c3-") or "concorde" in n.lower() for n in live.splitlines()):
        raise RuntimeError("other Concorde containers are running")
    m.update(started=time.time(), status="starting", networks={})
    m["cutoff"] = m["started"] + m["hours"] * 3600
    cutoff = dt.datetime.fromtimestamp(m["cutoff"], dt.timezone.utc).isoformat()
    m["proxy"] = m["prefix"] + "-transport"
    m["market"] = m["prefix"] + "-market"
    if PROFILE == "field":
        m["research"] = m["prefix"] + "-research"
    m["stop_unit"] = m["prefix"] + "-hard-stop"
    save(root / "manifest.json", m)
    # Independent absolute stop: does not depend on this loop or editable graph.
    run("sudo", "-n", "systemd-run", "--quiet", "--unit", m["stop_unit"],
        "--on-calendar", dt.datetime.fromtimestamp(m["cutoff"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "--timer-property=AccuracySec=1s", "--property=User=codex", "--property=Restart=on-failure", "--property=RestartSec=10s",
        "/usr/bin/python3", root / "assets/companies.py", "freeze", root)
    try:
        cfg = read(root / "market/config.json")
        cfg.update(started=m["started"], cutoff=m["cutoff"])
        for name in ALL:
            net = create_network(m["prefix"] + "-" + name + "-net")
            m["networks"][name] = net
            cfg["actors"][name]["product_host"] = net["company"]
            access = {"token": cfg["actors"][name]["token"], "url": "http://" + net["market"] + ":8081"}
            if PROFILE == "field":
                access["research_url"] = "http://" + str(ipaddress.ip_address(net["company"]) + 1) + ":8082"
            save(root / "access" / name / "access.json", access)
            save(root / "manifest.json", m)
        save(root / "market/config.json", cfg)
        first = m["networks"][next(iter(ALL))]
        run("docker", "run", "-d", "--name", m["market"], *restrictions("256m", ".5"),
            "--network", first["network"], "--ip", first["market"],
            "--mount", f"type=bind,source={root/'market'},target=/market-data",
            "--mount", f"type=bind,source={root/'assets/company_market.py'},target=/market.py,readonly",
            "--mount", f"type=bind,source={root/'assets/field_consumers.py'},target=/field_consumers.py,readonly",
            "--entrypoint", "python3", m["image_id"], "/market.py", "/market-data")
        from evals.transport import preview_mount_args
        run("docker", "run", "-d", "--name", m["proxy"], *restrictions("256m", ".5"), "--network", "bridge", *preview_mount_args(),
            "--mount", f"type=bind,source={root/'assets/transport.py'},target=/transport.py,readonly",
            "--entrypoint", "python3", m["image_id"], "/transport.py")
        if PROFILE == "field":
            run("docker", "run", "-d", "--name", m["research"], *restrictions("256m", ".5"), "--network", "bridge",
                "--env", "RESEARCH_DENY_IPS=" + run("hostname", "-I"),
                "--mount", f"type=bind,source={root/'assets/public_research.py'},target=/research.py,readonly",
                "--entrypoint", "python3", m["image_id"], "/research.py")
        for name, net in m["networks"].items():
            if net != first:
                run("docker", "network", "connect", "--ip", net["market"], net["network"], m["market"])
            run("docker", "network", "connect", "--ip", net["proxy"], net["network"], m["proxy"])
            if PROFILE == "field":
                run("docker", "network", "connect", "--ip", str(ipaddress.ip_address(net["company"]) + 1), net["network"], m["research"])
            run(BIN, "configure", root / "instances" / name, json.dumps({"freeze_at": cutoff}))
            args = ["docker", "create", "--name", m["containers"][name], *restrictions(), "--network", net["network"], "--ip", net["company"], "--dns", "127.0.0.1",
                "--tmpfs", "/home/node/.codex:rw,size=512m,uid=1000,gid=1000,mode=700", "--env", "CODEX_HOME=/home/node/.codex",
                "--env", f"HTTP_PROXY=http://{net['proxy']}:8080", "--env", f"HTTPS_PROXY=http://{net['proxy']}:8080", "--env", f"NO_PROXY=localhost,127.0.0.1,{net['market']}"]
            for source, target, writable in ((root / "instances" / name, "/instance", True), (root / "exchange" / name, "/exchange", False),
                    (root / "access" / name, "/market", False), (root / "assets/company_subject.py", "/subject.py", False),
                    (subscription_auth_file(), "/run/subscription-auth.json", False)):
                args += ["--mount", f"type=bind,source={source},target={target}" + ("" if writable else ",readonly")]
            # Launch subjects quiescent; qualify network before starting cognition.
            run(*args, "--entrypoint", "sleep", m["image_id"], "infinity")
            run("docker", "start", m["containers"][name])
        for name, cn in m["containers"].items():
            probe = "import socket,pathlib,sys;sys.path.insert(0,'/market');from client import request;assert request('account')['id']==sys.argv[1];assert not pathlib.Path('/market-data').exists();\ntry:\n socket.create_connection(('1.1.1.1',443),1);raise AssertionError('direct egress open')\nexcept OSError:pass\nprint('qualified')"
            run("docker", "exec", cn, "python3", "-c", probe, name)
            log(root, "isolation_qualified", instance=name)
        if qualification:
            qualify(root, m)
            return
        m["status"] = "running"
        save(root / "manifest.json", m)
        for name, cn in m["containers"].items():
            run("docker", "exec", "-d", cn, "python3", "/subject.py")
            log(root, "subject_started", instance=name, container=cn)
        supervise(root)
    except BaseException as error:
        log(root, "controller_failure", error=type(error).__name__ + ": " + str(error)[:1000])
        raise
    finally:
        freeze(root)


def qualify(root, m):
    """Mechanical fixture only. No cognition and no behavioral-success claim."""
    seller = m["containers"]["vector"]
    buyer = m["containers"][next(iter(CUSTOMERS))]
    assert process_present(seller, "sleep infinity")
    assert not process_present(seller, "concorde3 run /instance")
    run("docker", "exec", "-d", seller, "python3", "-m", "http.server", "8000", "--bind", "0.0.0.0", "--directory", "/exchange")
    time.sleep(1)
    code = """import sys,socket
sys.path.insert(0,'/market')
from client import request
def act(key,data):
 r=request('actions',data,key);assert 'error' not in r,r;return r
order=act('q-order',{'op':'order','seller':'vector','amount':3,'terms':'qualification fixture README access, not a business sale'})
use=act('q-use',{'op':'use','seller':'vector','path':'/README.md'})
assert use['status']=='returned' and use['http_status']==200,use
assert request('actions',{'op':'use','seller':'vector','path':'/README.md'},'q-use')==use
try:
 socket.create_connection((sys.argv[1],8000),1);raise AssertionError('private cross-network access')
except OSError: pass
print(order['id'],use['id'])
"""
    ids = run("docker", "exec", buyer, "python3", "-c", code, m["networks"]["vector"]["company"]).split()
    def act(container, key, data):
        result = json.loads(run("docker", "exec", container, "python3", "/market/client.py", "act", key, json.dumps(data)))
        if "error" in result:
            raise RuntimeError(result)
        return result
    act(seller, "q-delivery", {"op": "deliver", "order": ids[0], "reason": "fixture ready"})
    act(buyer, "q-accept", {"op": "accept", "order": ids[0], "use": ids[1], "reason": "fixture mechanical test, not business value", "alternative": "fixture only"})
    act(seller, "q-boost", {"op": "boost"})
    check_bounds(root, m, market_snapshot(root))
    assert state(root, "vector")["config"]["starts_per_hour"] == 8
    if PROFILE == "field":
        act(buyer, "q-buyer-boost", {"op": "boost"})
        check_bounds(root, m, market_snapshot(root))
        assert state(root, next(iter(CUSTOMERS)))["config"]["starts_per_hour"] == 10
        code = "import sys;sys.path.insert(0,'/market');from research import fetch;r=fetch('https://www.python.org/');assert r.get('status')==200,r;assert 'error' in fetch('http://127.0.0.1/');print('public read and private denial qualified')"
        run("docker", "exec", buyer, "python3", "-c", code, timeout=55)
        for name in CUSTOMERS:
            run("docker", "exec", "-d", m["containers"][name], "python3", "/instance/business.py", name)
        time.sleep(.5)
        jobs = {"parcel": {"orders": read(root / "exchange/parcel/orders.json")}, "beacon": {"subscribers": read(root / "exchange/beacon/subscribers.json")}}
        observed = json.loads(run("docker", "exec", "-i", m["market"], "python3", "/field_consumers.py", input=json.dumps(jobs)))
        assert all(x["fulfilled_requested_format"] for x in observed["parcel"]["results"]), observed
        assert len(observed["beacon"]["results"]) == 4, observed
        # Exercise the exact host world-tick adapter, without model starts.
        tick_manifest = dict(m, started=time.time() - 31)
        field_tick(root, tick_manifest)
        assert (root / "world/tick-0.json").exists()
    assert all(not state(root, name)["activations"] for name in ALL)
    # Test independent systemd stop without waiting for the pilot deadline.
    run("sudo", "-n", "systemd-run", "--quiet", "--unit", m["prefix"] + "-qualification-stop", "--on-active", "1s",
        "--timer-property=AccuracySec=1s", "/usr/bin/docker", "stop", "-t", "1", seller)
    for _ in range(12):
        if run("docker", "inspect", "--format", "{{.State.Running}}", seller) == "false":
            break
        time.sleep(.5)
    else:
        raise RuntimeError("independent stop watchdog did not stop fixture")
    save(root / "qualification.json", {"passed": True, "model_calls": 0,
         "checks": ["isolated commercial clients", "process-health probe", "cross-network direct access blocked", "gateway actual-use receipt", "idempotent replay", "escrow acceptance", "paid capacity applied", "independent systemd stop"] + (["public web retrieval", "private destination blocked", "customer capacity lease", "existing-business consumer calls"] if PROFILE == "field" else [])})
    log(root, "mechanical_qualification_passed", behavioral_evidence=False)


def market_snapshot(root):
    db = sqlite3.connect(f"file:{root/'market/market.sqlite'}?mode=ro", uri=True, timeout=20)
    try:
        accounts = dict(db.execute("SELECT id,balance FROM accounts"))
        records = {}
        for kind, body in db.execute("SELECT kind,body FROM records"):
            records.setdefault(kind, []).append(json.loads(body))
        event_counts = [dict(actor=a, kind=k, count=n) for a, k, n in db.execute("SELECT actor,kind,count(*) FROM events GROUP BY actor,kind")]
        return {"accounts": accounts, "records": records, "event_counts": event_counts}
    finally:
        db.close()


def capacity(name, records, at):
    if name in CUSTOMERS and PROFILE != "field":
        return 6
    base = 6 if name in CUSTOMERS else 4
    return base + 4 if any(b["actor"] == name and b["until"] > at for b in records.get("boost", [])) else base


def state(root, name):
    path = root / "instances" / name / ".concorde2/state.json"
    if path.resolve() != path:
        raise RuntimeError("unexpected state symlink")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise RuntimeError("state exceeds experimental read ceiling")
    return read(path)


def same_deadline(text, timestamp):
    # JSON float → datetime → Go RFC3339 normalizes sub-microsecond precision.
    return abs(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() - timestamp) < .00001


def process_present(container, command):
    # Docker needs a PID column to map host ps output back into the container.
    return command in run("docker", "top", container, "-eo", "pid,args")


def check_bounds(root, m, snapshot):
    now = time.time()
    if sum(snapshot["accounts"].values()) != m.get("currency_supply", 240):
        raise RuntimeError("currency conservation failure")
    for name, cn in m["containers"].items():
        st = state(root, name)
        cfg = st["config"]
        desired = capacity(name, snapshot["records"], now)
        expected = {"model": m["model"], "effort": m["effort"], "concurrency": 1, "harness": "codex", "deadline_seconds": 600,
                    "external_sandbox": True, "workspace": True}
        permitted = (4, 8) if name in VENTURES else ((6, 10) if PROFILE == "field" else (6,))
        if any(cfg.get(k) != v for k, v in expected.items()) or cfg["starts_per_hour"] not in permitted:
            raise RuntimeError("unexpected runtime configuration: " + name)
        if not same_deadline(cfg["freeze_at"], m["cutoff"]):
            raise RuntimeError("changed finite deadline: " + name)
        recent = sum(dt.datetime.fromisoformat(a["started"].replace("Z", "+00:00")).timestamp() > now - 3600 for a in st["activations"].values())
        if recent > max(permitted):
            raise RuntimeError("activation ceiling exceeded: " + name)
        if cfg["starts_per_hour"] != desired:
            run("docker", "exec", cn, "concorde3", "configure", "/instance", json.dumps({"starts_per_hour": desired}))
            log(root, "capacity_applied", instance=name, starts_per_hour=desired, recent_starts=recent)


def apply_shocks(root, elapsed):
    for hour, title, changes in SHOCKS:
        marker = root / f"shock-{hour}.json"
        if elapsed < hour * 3600 or marker.exists():
            continue
        for name, text in changes.items():
            path = root / "exchange" / name / "updates.json"
            updates = read(path)
            if not any(x["id"] == f"hour-{hour}" for x in updates):
                updates.append({"id": f"hour-{hour}", "at": time.time(), "title": title, "text": text, "provenance": "synthetic customer correspondence"})
                save(path, updates)
        save(marker, {"at": time.time(), "title": title})
        log(root, "circumstances_changed", hour=hour, title=title, recipients=list(changes), private_graph_wakes=False)


def field_tick(root, m):
    from field_seeds import orders
    elapsed = time.time() - m["started"]
    tick = int(elapsed // 600)
    marker = root / "world" / f"tick-{tick}.json"
    if elapsed < 30 or marker.exists():
        return
    current_orders = orders(tick)
    save(root / "exchange/parcel/orders.json", current_orders)
    jobs = {"parcel": {"orders": current_orders}, "beacon": {"subscribers": read(root / "exchange/beacon/subscribers.json")}}
    # Public product reads only, inside the market boundary; never company code on host.
    result = json.loads(run("docker", "exec", "-i", m["market"], "python3", "/field_consumers.py", input=json.dumps(jobs), timeout=25))
    for name, evidence in result.items():
        path = root / "exchange" / name / "customer-feedback.json"
        previous = read(path)
        previous.append({"tick": tick, **evidence})
        save(path, previous)
    save(marker, {"tick": tick, "at": time.time(), "consumer_attempts": result})
    log(root, "operating_world_tick", tick=tick, order_records=len(current_orders), evidence=str(marker.relative_to(root)), private_graph_wakes=False)


def observe(root):
    m = read(root / "manifest.json")
    market = market_snapshot(root)
    companies = {}
    for name, cn in m["containers"].items():
        st = state(root, name)
        acts = list(st["activations"].values())
        programs = st["programs"]
        companies[name] = {"mode": st["mode"], "starts": len(acts), "limit": st["config"]["starts_per_hour"],
            "status_counts": {status: sum(a["status"] == status for a in acts) for status in set(a["status"] for a in acts)},
            "usage": {key: sum(a["usage"].get(key, 0) for a in acts) for key in ("input", "cached", "output")},
            "unmeasured": sum(a["usage"].get("quality") != "measured" for a in acts),
            "nodes": len(st["nodes"]), "items": len(st["items"]), "edges": len(st["edges"]),
            "programs": programs,
            "intentions": [{"id": i["id"], "text": i["text"], "attention": i["attention"]} for i in st["items"].values() if i.get("attention")],
            "last_activations": [{k: a.get(k) for k in ("id", "started", "finished", "status", "phase", "summary", "work_summary", "completion")} for a in sorted(acts, key=lambda a: a["started"])[-4:]]}
    orders = market["records"].get("order", [])
    result = {"at": time.time(), "companies": companies, "accounts": market["accounts"], "market_events": market["event_counts"],
        "offers": market["records"].get("offer", []), "needs": market["records"].get("need", []),
        "orders": orders, "reviews": market["records"].get("review", []), "boosts": market["records"].get("boost", []),
        "uses": [{k: v for k, v in x.items() if k != "body"} for x in market["records"].get("use", [])],
        "accepted_simulated_customer_orders": sum(o["status"] == "accepted" and o["buyer"] in CUSTOMERS for o in orders),
        "accepted_venture_orders": sum(o["status"] == "accepted" and o["buyer"] in VENTURES for o in orders),
        "external_customers": 0, "real_revenue": 0,
        "interpretation": "Automatic evidence checkpoint, not a competence judgment. Review actual product-use bodies and artifacts before crediting value."}
    try:
        result["resources"] = [json.loads(x) for x in run("docker", "stats", "--no-stream", "--format", "{{json .}}", *m["containers"].values()).splitlines()]
    except Exception:
        result["resources_unavailable"] = True
    save(root / "latest-observation.json", result)
    save(root / "observations" / (str(int(result["at"])) + ".json"), result)
    log(root, "checkpoint", observation=f"observations/{int(result['at'])}.json", starts={n: c["starts"] for n, c in companies.items()},
        accepted_simulated_customer_orders=result["accepted_simulated_customer_orders"], accepted_venture_orders=result["accepted_venture_orders"])
    return result


def supervise(root):
    m = read(root / "manifest.json")
    next_observation = 0
    while time.time() < m["cutoff"]:
        snapshot = market_snapshot(root)
        check_bounds(root, m, snapshot)
        if PROFILE == "field":
            field_tick(root, m)
        # Container sleep is not a healthy supervisor. Detect failed preflight
        # or a crashed inner loop without manufacturing business activations.
        if time.time() - m["started"] > 120:
            for name, cn in m["containers"].items():
                if not (root / "instances" / name / ".concorde2/company-preflight.json").exists():
                    raise RuntimeError("missing subscription preflight: " + name)
                if state(root, name)["mode"] != "running":
                    raise RuntimeError("unexpected company runtime stop: " + name)
                if not process_present(cn, "concorde3 run /instance"):
                    raise RuntimeError("company supervisor exited: " + name)
        apply_shocks(root, time.time() - m["started"])
        if time.time() >= next_observation:
            observe(root)
            next_observation = time.time() + 1800
        time.sleep(min(10, max(0, m["cutoff"] - time.time())))


def freeze(root):
    with (root / "freeze.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        m = read(root / "manifest.json")
        names = [*m["containers"].values(), m.get("market"), m.get("proxy"), m.get("research")]
        names = [name for name in names if name]
        existing = []
        for name in names:
            p = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name], text=True, capture_output=True, timeout=10)
            if p.returncode == 0:
                existing.append(name)
        if existing:
            run("docker", "stop", "-t", "3", *existing, timeout=40)
        errors = []
        def freeze_one(name):
            try:
                run("docker", "run", "--rm", *restrictions("256m", ".5"), "--network", "none",
                    "--mount", f"type=bind,source={root/'instances'/name},target=/instance", m["image_id"], "freeze", "/instance")
            except Exception as error:
                return name + ": " + str(error)[:200]
        with ThreadPoolExecutor(max_workers=6) as pool:
            errors += [e for e in pool.map(freeze_one, ALL) if e]
        for name in existing:
            if run("docker", "inspect", "--format", "{{.State.Running}}", name) != "false":
                errors.append("container still running: " + name)
        m["status"] = "frozen" if not errors else "freeze_error"
        save(root / "manifest.json", m)
        save(root / "closeout.json", {"at": time.time(), "frozen": not errors, "containers": existing, "errors": errors})
        log(root, "frozen", errors=errors)
        if errors:
            raise RuntimeError("; ".join(errors))
        try:
            observe(root)
        except Exception as error:
            log(root, "closeout_observation_error", error=str(error)[:200])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("prepare", "run", "qualify", "observe", "freeze"))
    p.add_argument("root", type=lambda value: Path(value).resolve())
    p.add_argument("--hours", type=float, default=12)
    p.add_argument("--image", default="concorde3:lab-current")
    p.add_argument("--profile", choices=("classic", "field"), default="classic")
    args = p.parse_args()
    os.umask(0o077)
    set_profile(args.profile if args.action == "prepare" else read(args.root / "manifest.json").get("profile", "classic"))
    if args.action == "prepare":
        prepare(args.root, args.hours, args.image)
    elif args.action in ("run", "qualify"):
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        launch(args.root, qualification=args.action == "qualify")
    elif args.action == "freeze":
        freeze(args.root)
    else:
        result = observe(args.root)
        print(json.dumps({"accounts": result["accounts"], "companies": {n: {k: c[k] for k in ("mode", "starts", "status_counts", "limit")} for n, c in result["companies"].items()},
                          "accepted_simulated_customer_orders": result["accepted_simulated_customer_orders"], "accepted_venture_orders": result["accepted_venture_orders"]}, indent=2))


if __name__ == "__main__":
    main()

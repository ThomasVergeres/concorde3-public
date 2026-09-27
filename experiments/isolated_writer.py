"""Candidate-only filesystem and model-only egress for the optional code writer."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

from evals.lab import command, create_network, save
from worlds.auth import subscription_auth_file
from .discrepancy_transport import stop_candidate_container


def verify_host_isolation(network):
    options = network.get("Options", {})
    if not network.get("Internal") or any(options.get("com.docker.network.bridge.gateway_mode_"+family) != "isolated" for family in ("ipv4", "ipv6")):
        raise RuntimeError("writer requires isolated host gateway modes")
    bridge = options.get("com.docker.network.bridge.name", "br-"+network["Id"][:12])
    addresses = json.loads(command(["ip", "-j", "address", "show", "dev", bridge]))
    if len(addresses) != 1 or addresses[0].get("addr_info"):
        raise RuntimeError("writer bridge missing or exposes host addresses")
    return bridge, addresses


def worker_arguments(worktree, sessions, worker, auth, image, network, proxy_ip, name):
    worktree, sessions, worker, auth = (Path(p).resolve() for p in (worktree, sessions, worker, auth))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("pinned writer image required; no host fallback")
    if not re.fullmatch(r"c3-candidate-check-[0-9a-f]{16}", name):
        raise ValueError("owned writer container name required")
    if not worktree.is_dir() or not sessions.is_dir() or not worker.is_file() or not auth.is_file():
        raise ValueError("exact existing writer mounts required")
    return ["docker", "create", "--name", name, "--label", "concorde.lab=true",
        "--network", network, "--dns", "127.0.0.1", "--read-only", "--user", f"{os.getuid()}:{os.getgid()}",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--memory", "4g", "--cpus", "2", "--tmpfs", "/tmp:rw,exec,size=2g,mode=1777",
        "--env", "CODEX_HOME=/home/node/.codex", "--env", f"HTTPS_PROXY=http://{proxy_ip}:8080",
        "--env", "CONCORDE_ISOLATED_WRITER=1",
        "--env", f"HTTP_PROXY=http://{proxy_ip}:8080", "--workdir", "/candidate",
        "--mount", f"type=bind,source={worktree},target=/candidate",
        "--mount", f"type=bind,source={sessions},target=/home/node/.codex",
        "--mount", f"type=bind,source={worker},target=/run/builder_worker.py,readonly",
        "--mount", f"type=bind,source={auth},target=/run/subscription-auth.json,readonly",
        "--entrypoint", "sleep", image, "infinity"]


class IsolatedWriter:
    def __init__(self, root, call_id, worktree, image, hard_until, auth=None):
        self.root, self.worktree = Path(root).resolve(), Path(worktree).resolve()
        if not self.worktree.is_relative_to(self.root/"cases") or self.worktree == self.root/"cases":
            raise ValueError("writer source must be a dedicated campaign candidate directory")
        self.call_id, self.image, self.hard_until, self.auth = call_id, image, hard_until, Path(auth or subscription_auth_file())
        self.worker = "c3-candidate-check-"+secrets.token_hex(8)
        self.proxy = "c3-candidate-check-"+secrets.token_hex(8)
        self.network = self.worker+"-net"
        self.unit = self.worker+"-stop"
        self.inventory = self.root/"container-active"/(call_id+".json")
        self.session = self.root/"builder-sessions"/call_id
        self.stopped = False

    def start(self):
        if time.time() >= self.hard_until-30:
            raise RuntimeError("insufficient writer time before fixed cutoff")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image or ""):
            raise ValueError("pinned writer image required; no host fallback")
        self.session.mkdir(parents=True, mode=0o700)
        home = self.session/"codex-home"; home.mkdir(mode=0o700)
        save(self.inventory, {"call": self.call_id, "containers": [self.worker, self.proxy],
            "network": self.network, "until": self.hard_until, "purpose": "isolated candidate writer"})
        cutoff = dt.datetime.fromtimestamp(self.hard_until, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        command(["sudo", "-n", "systemd-run", "--quiet", "--unit", self.unit, "--on-calendar", cutoff,
            "--timer-property=AccuracySec=1s", "docker", "stop", "-t", "2", self.worker, self.proxy])
        create_network(self.network, isolate_host=True)
        network = json.loads(command(["docker", "network", "inspect", self.network]))[0]
        # Linux may autoconfigure an IPv6 link-local host address even on an
        # isolated Docker bridge. Disable IPv6 only on this newly owned bridge,
        # not globally or on any existing fleet network.
        if not re.fullmatch(r"[0-9a-f]{64}", network.get("Id", "")) or network.get("Name") != self.network or network.get("Options", {}).get("com.docker.network.bridge.name"):
            raise RuntimeError("cannot establish ownership of writer bridge")
        bridge = "br-"+network["Id"][:12]
        command(["sudo", "-n", "sysctl", "-w", "net.ipv6.conf."+bridge+".disable_ipv6=1"])
        transport = Path(__file__).resolve().parents[1]/"evals/transport.py"
        from evals.transport import preview_mount_args
        command(["docker", "run", "-d", "--name", self.proxy, "--network", "bridge", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "128m", "--cpus", ".25", "--pids-limit", "128",
            "--mount", f"type=bind,source={transport},target=/transport.py,readonly", *preview_mount_args(),
            "--entrypoint", "python3", self.image, "/transport.py"])
        command(["docker", "network", "connect", self.network, self.proxy])
        ip = command(["docker", "inspect", self.proxy, "--format", '{{(index .NetworkSettings.Networks "'+self.network+'").IPAddress}}'])
        worker = Path(__file__).with_name("builder_worker.py")
        args = worker_arguments(self.worktree, home, worker, self.auth, self.image, self.network, ip, self.worker)
        command(args); command(["docker", "start", self.worker])
        network = json.loads(command(["docker", "network", "inspect", self.network]))[0]
        bridge, addresses = verify_host_isolation(network)
        # Codex commands commonly use login shells, which reset PATH. Qualify
        # the actual shell/toolchain boundary, not only image ENV or an outer test.
        toolchain = command(["docker", "exec", self.worker, "/bin/bash", "-lc",
            "command -v go && command -v gofmt && go version && go env GOTOOLCHAIN GOPROXY GOSUMDB CGO_ENABLED"], timeout=20)
        if toolchain.splitlines()[-4:] != ["local", "off", "off", "0"]:
            raise RuntimeError("writer login shell lacks qualified offline Go toolchain")
        save(self.session/"isolation.json", {"image": self.image, "worker": self.worker, "proxy": self.proxy,
            "network": self.network, "worktree": str(self.worktree), "until": self.hard_until,
            "host_bridge": bridge, "host_bridge_addresses": addresses,
            "login_shell_toolchain": toolchain,
            "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
            "transport_sha256": hashlib.sha256(transport.read_bytes()).hexdigest(), "container_command": args,
            "scope": "Candidate files and isolated session only; no host home, fleet, Docker socket or current campaign holdout mounted. Model-only CONNECT egress; no API fallback. Candidate diff still requires independent path/test/semantic review."})
        return ["docker", "exec", "-i", self.worker, "python3", "/run/builder_worker.py"]

    def stop(self):
        receipts = [stop_candidate_container(name) for name in (self.worker, self.proxy)]
        self.stopped = all(r["verified_stopped"] for r in receipts)
        save(self.session/"closure.json", {"at": time.time(), "containers": receipts, "verified_stopped": self.stopped})
        if self.stopped:
            self.inventory.unlink(missing_ok=True)
            subprocess.run(["sudo", "-n", "systemctl", "stop", self.unit+".timer"], capture_output=True, timeout=15)
        return self.stopped

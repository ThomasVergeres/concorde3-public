"""Bounded subscription-only model calls for the external discrepancy lab."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time

from evals.lab import save
from worlds import budget
from worlds.auth import subscription_auth_file
from .discrepancy_store import DiscrepancyStore


OUTER_SCHEMA = {"type": "object", "properties": {
    "actions": {"type": "array", "items": {"type": "object", "properties": {},
        "required": [], "additionalProperties": False}, "maxItems": 0},
    "note": {"type": "string"}, "finish": {"type": "boolean"}},
    "required": ["actions", "note", "finish"], "additionalProperties": False}


def usage(events):
    values = []
    for line in events.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") == "turn.completed":
            values.append(row.get("usage"))
    return values or None


def recorded_error(events):
    """Return the last structured Codex error when stderr is empty."""
    result = None
    for line in events.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") == "error" and isinstance(row.get("message"), str):
            result = row["message"]
        elif row.get("type") == "turn.failed":
            error = row.get("error", {})
            if isinstance(error.get("message"), str):
                result = error["message"]
    return (result or "")[-1000:]


def process_start_ticks(pid):
    """Linux start-time identity, preventing stale PID reuse during cleanup."""
    text = Path(f"/proc/{pid}/stat").read_text()
    return int(text.rsplit(")", 1)[1].split()[19])


def same_process(pid, ticks):
    try:
        return process_start_ticks(pid) == ticks
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False


def live_group_members(pgid):
    """A dead CLI leader is not proof its model subprocesses have exited."""
    members = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal(): continue
        try:
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
            members[int(path.name)] = int(fields[19])
    return members


def stop_owned_group(pid, ticks, grace=10, reap=lambda: None):
    members = live_group_members(pid)
    if not members: return True
    if not same_process(pid, ticks): return False  # No authority over a reused/orphaned group.
    os.killpg(pid, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        reap()
        current = live_group_members(pid)
        if not current: return True
        time.sleep(.05)
    current = live_group_members(pid)
    if not current: return True
    # Retain an identity witness even if SIGTERM killed the group leader.
    if not any(current.get(p) == start for p, start in members.items()): return False
    os.killpg(pid, signal.SIGKILL)
    deadline = time.time() + 5
    while time.time() < deadline:
        reap()
        if not live_group_members(pid): return True
        time.sleep(.05)
    return False


def terminate_group(process, ticks, grace=10):
    stopped = stop_owned_group(process.pid, ticks, grace, process.poll)
    if stopped:
        # A zombie cannot execute, but reap our direct child before advertising
        # complete cleanup or letting Popen warn about an outstanding process.
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: return False
    return stopped


class MetaDriver:
    """No-tool Codex invocation recorded under a private campaign root.

    Calls share the campaign's dispatch ledger but never use a participant/world
    call record.  The controller service cgroup and an explicit process-group
    inventory provide bounded shutdown even if this object is interrupted.
    """
    def __init__(self, root, store: DiscrepancyStore, *, model="gpt-5.6-sol",
                 effort="high", timeout=180, cap=720, concurrency=24,
                 auth=None, hard_until=None, codex_binary=None,
                 maximum_calls=None, response_schema=None):
        self.root, self.store = Path(root).resolve(), store
        self.model, self.effort, self.timeout = model, effort, timeout
        self.cap, self.concurrency = cap, concurrency
        manifest_path = self.root/"cohort.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        self.maximum_calls = maximum_calls if maximum_calls is not None else manifest.get("limits", {}).get("maximum_meta_calls", 40)
        self.response_schema = OUTER_SCHEMA if response_schema is None else response_schema
        self.auth, self.hard_until = Path(auth or subscription_auth_file()).resolve(), hard_until
        self.codex = codex_binary or shutil.which("codex") or str(Path.home()/'.local/bin/codex')
        if not Path(self.codex).is_file():
            raise RuntimeError("absolute Codex CLI unavailable")

    def remaining(self):
        remaining = self.timeout if self.hard_until is None else self.hard_until - time.time()
        if remaining <= 0:
            raise RuntimeError("meta execution boundary reached")
        return min(self.timeout, remaining)

    def prepare_call(self, base, prompt):
        """Setup is part of the recorded call, not an untracked preamble."""
        base.mkdir(parents=True, mode=0o700)
        save(base / "schema.json", self.response_schema)
        (base / "prompt.txt").write_text(prompt)
        (base / "prompt.txt").chmod(0o600)
        codex_home = base / "codex-home"
        codex_home.mkdir(mode=0o700)
        (codex_home / "auth.json").symlink_to(self.auth)
        env = {**os.environ, "CODEX_HOME": str(codex_home)}
        check = subprocess.run([self.codex, "login", "status"], env=env,
                               capture_output=True, text=True, timeout=min(20, self.remaining()))
        if check.returncode or "Logged in using ChatGPT" not in check.stdout + check.stderr:
            raise RuntimeError("subscription authentication required; no API fallback")
        command = [self.codex, "--config", 'forced_login_method="chatgpt"',
            "--config", 'model_provider="openai"', "--config", "features.apps=false",
            "--config", "features.skills=false", "--config", "features.multi_agent=false",
            "--config", "features.shell_tool=false", "--config", "memories.use_memories=false",
            "--config", "memories.generate_memories=false", "--config",
            "model_reasoning_effort=" + json.dumps(self.effort), "--ask-for-approval", "never",
            "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--json", "--model", self.model, "--sandbox", "read-only",
            "--output-schema", str(base / "schema.json"),
            "--output-last-message", str(base / "result.json"), "-"]
        return command, env

    def __call__(self, purpose, prompt):
        if (self.model not in {"gpt-5.6-sol", "gpt-5.6-luna"} or
                self.effort not in {"high", "xhigh"} or
                (self.model == "gpt-5.6-luna" and self.effort != "xhigh")):
            raise ValueError("discrepancy campaign permits Luna xhigh or Sol high/xhigh only")
        self.remaining()
        # Casework cannot spend the model calls reserved for unattempted cadence
        # reviews. Count+reserve remains atomic in begin_call across callers.
        reserved = len(self.store.rows("rounds", "number>0 AND status='scheduled'")) if not purpose.startswith(("review:", "qualification:")) else 0
        call_id = self.store.begin_call(purpose, self.model, self.effort, maximum=self.maximum_calls-reserved)
        base = self.root / "meta-calls" / call_id
        ledger = self.root / "world-budget" / "calls.jsonl"
        process = None
        start_ticks = None
        reserved = False
        try:
            command, env = self.prepare_call(base, prompt)
            self.remaining()  # Authentication/setup can consume the remaining window.
            budget.reserve(call_id, file=ledger, cap=self.cap, concurrency=self.concurrency)
            reserved = True
            effective = self.remaining()  # Admission can block on the shared ledger lock.
            save(base / "provenance.json", {"model": self.model, "effort": self.effort,
                "auth": "ChatGPT subscription, fail closed", "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "schema_sha256": hashlib.sha256((base / "schema.json").read_bytes()).hexdigest(),
                "command_contract": "forced ChatGPT/OpenAI provider; ignored user config/rules; apps, skills, multi-agent, shell and memories disabled",
                "timeout_seconds": effective})
            self.remaining()
            process = subprocess.Popen(command, cwd=base, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            start_ticks = process_start_ticks(process.pid)
            self.store.call_running(call_id, str(base / "provenance.json"))
            save(self.root / "meta-active" / (call_id + ".json"), {"call": call_id,
                "pid": process.pid, "pgid": process.pid, "started": time.time(),
                "process_start_ticks": start_ticks,
                "model": self.model, "effort": self.effort, "purpose": purpose})
            try:
                stdout, stderr = process.communicate(prompt, timeout=self.remaining())
            except subprocess.TimeoutExpired:
                terminate_group(process, start_ticks)
                stdout, stderr = process.communicate(timeout=5)
                (base / "events.partial.jsonl").write_text(stdout)
                (base / "stderr.partial.txt").write_text(stderr[-8000:])
                raise RuntimeError("meta model deadline reached")
            (base / "events.jsonl").write_text(stdout)
            (base / "stderr.txt").write_text(stderr[-8000:])
            result = json.loads((base / "result.json").read_text()) if (base / "result.json").exists() else None
            if process.returncode or not result:
                detail = stderr[-1000:] or recorded_error(stdout)
                raise RuntimeError("meta model failed: " + (detail or "no structured error emitted"))
            measured = usage(stdout)
            self.store.finish_call(call_id, "completed", usage=measured,
                                   evidence_ref=str(base / "result.json"))
            return result, {"call_id": call_id, "usage": measured,
                            "response_ref": str(base / "result.json")}
        except BaseException as error:
            row = self.store.rows("calls", "id=?", (call_id,))[0]
            if row["status"] in ("reserved", "running"):
                self.store.finish_call(call_id, "failed", error=str(error)[:1000],
                                       evidence_ref=str(base))
            raise
        finally:
            active = self.root / "meta-active" / (call_id + ".json")
            stopped = True
            if process is not None:
                stopped = terminate_group(process, start_ticks)
            if stopped and active.exists():
                active.unlink()
            if reserved and stopped:
                budget.release(call_id, file=ledger)


def candidate_container(command):
    """Resolve only an engine-minted candidate container, never a broad target."""
    command = [str(a) for a in command]
    if command[:2] != ["docker", "run"] or "--name" not in command:
        return None
    index = command.index("--name") + 1
    if index < len(command) and re.fullmatch(r"c3-candidate-check-[0-9a-f]{16}", command[index]):
        return command[index]
    return None


def stop_candidate_container(name):
    if not re.fullmatch(r"c3-candidate-check-[0-9a-f]{16}", name):
        raise ValueError("engine-owned candidate container required")
    receipt = {"container": name, "verified_stopped": False}
    try:
        stopped = subprocess.run(["docker", "stop", "-t", "5", name],
            capture_output=True, text=True, timeout=20)
        receipt["stop_returncode"] = stopped.returncode
        receipt["stop_error"] = stopped.stderr[-1000:]
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt["stop_error"] = str(error)[-1000:]
    try:
        # --rm can remove a stopped container. A successful daemon query proving
        # absence is valid; a daemon/inspection error is not proof of absence.
        inspected = subprocess.run(["docker", "ps", "--all", "--filter",
            f"name=^/{name}$", "--format", "{{.State}}"],
            capture_output=True, text=True, timeout=20)
        states = inspected.stdout.strip().splitlines()
        receipt.update({"inspection_returncode": inspected.returncode,
            "states": states, "inspection_error": inspected.stderr[-1000:],
            "verified_stopped": inspected.returncode == 0 and
                (not states or states in (["exited"], ["dead"], ["created"]))})
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt["inspection_error"] = str(error)[-1000:]
    return receipt


def stop_active(root, *, preserve_purposes=()):
    """Terminate only inventoried campaign meta process groups."""
    root = Path(root).resolve()
    stopped, errors, preserved = [], [], []
    paths = []
    for directory in ("meta-active", "jobs-active", "repair-active"):
        paths.extend((root / directory).glob("*.json"))
    for path in sorted(paths):
        try:
            record = json.loads(path.read_text())
            if path.parent.name == "meta-active" and record.get("purpose") in preserve_purposes:
                preserved.append(record)
                continue
            pid, ticks = int(record["pgid"]), int(record["process_start_ticks"])
            absent = not live_group_members(pid)
            try:
                group_stopped = absent or stop_owned_group(pid, ticks)
            except ProcessLookupError:
                group_stopped = not live_group_members(pid)
            name = candidate_container(record.get("command", []))
            container = stop_candidate_container(name) if name else None
            if record.get("containers"):
                members = record["containers"]
                if not isinstance(members, list) or not 1 <= len(members) <= 2:
                    raise ValueError("bounded owned container inventory required")
                receipts = [stop_candidate_container(member) for member in members]
                container = {"verified_stopped": all(r["verified_stopped"] for r in receipts), "containers": receipts}
            if not group_stopped:
                errors.append({"path": str(path), "error": "process group did not exit or identity could not be verified"})
            elif container and not container["verified_stopped"]:
                errors.append({"path": str(path), "error": "candidate container cleanup unverified",
                    "container_cleanup": container})
            else:
                stopped.append({**record, "already_absent_or_reused": absent,
                    "container_cleanup": container})
                if path.parent.name == "meta-active" and record.get("call"):
                    budget.release(record["call"], file=root / "world-budget/calls.jsonl")
                path.unlink(missing_ok=True)
        except Exception as error:
            errors.append({"path": str(path), "error": str(error)})
    # Covers setup failures before a CLI process exists, as well as proxies.
    for path in sorted((root/"container-active").glob("*.json")):
        try:
            record = json.loads(path.read_text()); names = record["containers"]
            if not isinstance(names, list) or not 1 <= len(names) <= 2:
                raise ValueError("bounded owned container inventory required")
            receipts = [stop_candidate_container(name) for name in names]
            if not all(r["verified_stopped"] for r in receipts):
                errors.append({"path": str(path), "error": "container cleanup unverified", "containers": receipts})
                continue
            stopped.append({**record, "container_cleanup": receipts})
            if record.get("call") and not (root/"meta-active"/(record["call"]+".json")).exists():
                budget.release(record["call"], file=root/"world-budget/calls.jsonl")
            path.unlink(missing_ok=True)
        except Exception as error:
            errors.append({"path": str(path), "error": str(error)})
    return {"at": time.time(), "stopped": stopped, "errors": errors, "preserved": preserved}

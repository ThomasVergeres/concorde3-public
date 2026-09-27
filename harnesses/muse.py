"""Bounded Muse command harness; API credentials belong in a host-side broker.

Protocol 2: one command invocation per phase, persisted visible conversation across
work/rectification, actual Concorde MCP, and per-request usage/transcripts. No API
retry or replay of pending effects. Chat Completions does not expose Muse's private
reasoning state; visible conversation continuity is not encrypted reasoning replay.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

MODEL = "muse-spark-1.3-contributor"
MAX_RESPONSE = 8 * 1024 * 1024
MAX_SESSION = 32 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def atomic(path, value):
    data = encoded(value)
    if len(data) > MAX_SESSION:
        raise ValueError("conversation exceeds explicit 32 MiB bound; not silently compacted")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".muse-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_log(path, value):
    value = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), **value}
    with path.open("ab") as out:
        out.write(encoded(value) + b"\n")
        out.flush()
        os.fsync(out.fileno())


def payload_record(path, value):
    """Keep normal JSONL events bounded while retaining exact API evidence."""
    if path.exists():
        raise ValueError("API payload record already exists; refusing overwrite")
    atomic(path, value)
    data = encoded(value)
    return {"path": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def remaining(deadline):
    seconds = deadline - time.time()
    if seconds <= 0:
        raise TimeoutError("phase deadline reached")
    return seconds


def clean_environment():
    # No provider key or experiment broker bearer is inherited by MCP/shell.
    # The broker's actual API key must never enter this container at all.
    return {k: v for k, v in os.environ.items()
            if k not in {"META_API_KEY", "MODEL_API_KEY", "MUSE_SPARK_API_KEY",
                         "OPENAI_API_KEY", "CONCORDE3_MUSE_BROKER_TOKEN"}}


def stop_tree(process):
    """Best-effort timeout cleanup; container/group freeze is the stronger boundary."""
    descendants = {process.pid}
    for _ in range(16):
        before = len(descendants)
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                value = path.read_text()
                fields = value[value.rfind(")") + 2:].split()
                if int(fields[1]) in descendants:
                    descendants.add(int(path.parent.name))
            except (OSError, ValueError, IndexError):
                pass
        if len(descendants) == before:
            break
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def shell(command, workspace, deadline, timeout=60, cap=65536):
    if not isinstance(command, str) or not command or len(command) > 65536:
        raise ValueError("command must be a nonempty string of at most 65536 characters")
    limit = min(deadline, time.time() + max(1, min(float(timeout), 60)))
    process = subprocess.Popen(["bash", "--noprofile", "--norc", "-c", command],
                               cwd=workspace, env=clean_environment(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    data = bytearray()
    truncated = False
    timed_out = False
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.time() >= limit:
                timed_out = True
                stop_tree(process)
                break
            for key, _ in selector.select(min(0.2, remaining(limit))):
                block = os.read(key.fileobj.fileno(), 8192)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    room = cap - len(data)
                    data.extend(block[:room])
                    truncated |= len(block) > room
        if process.poll() is None:
            process.wait(timeout=max(0.01, limit - time.time()))
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_tree(process)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            stop_tree(process)
    return {"output": data.decode("utf-8", "replace"), "exit_code": process.returncode,
            "truncated": truncated, "timed_out": timed_out}


class MCP:
    def __init__(self, binary, workspace, activation, deadline):
        self.deadline = deadline
        self.sequence = 0
        self.pending = bytearray()
        self.process = subprocess.Popen([binary, "mcp", str(workspace), activation],
                                        env=clean_environment(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.call("initialize", {})

    def call(self, method, params):
        self.sequence += 1
        self.process.stdin.write(encoded({"jsonrpc": "2.0", "id": self.sequence,
                                          "method": method, "params": params}) + b"\n")
        self.process.stdin.flush()
        while b"\n" not in self.pending:
            if not self.selector.select(min(1, remaining(self.deadline))):
                continue
            part = os.read(self.process.stdout.fileno(), 8192)
            if not part:
                raise RuntimeError("MCP exited without returning its response")
            self.pending.extend(part)
            if len(self.pending) > MAX_RESPONSE:
                raise ValueError("MCP response exceeds 8 MiB bound")
        line, _, rest = self.pending.partition(b"\n")
        self.pending = bytearray(rest)
        response = json.loads(line)
        if response.get("id") != self.sequence:
            raise ValueError("MCP response id mismatch")
        if "error" in response:
            raise ValueError("MCP rejected request: " + str(response["error"]))
        return response["result"]

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                stop_tree(self.process)
        self.process.stdout.close()


def parse_usage(response):
    usage = response.get("usage")
    unavailable = {"quality": "unavailable", "basis": "api", "input": 0,
                   "cached": 0, "output": 0}
    if not isinstance(usage, dict):
        return unavailable
    inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details")
    if details is not None and not isinstance(details, dict):
        return unavailable
    cached = (details or {}).get("cached_tokens")
    if any(type(value) is not int or value < 0 for value in (inp, out)):
        return unavailable
    if cached is not None and (type(cached) is not int or cached < 0 or cached > inp):
        return unavailable
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != inp + out):
        return unavailable
    return {"quality": "measured" if cached is not None else "partial", "basis": "api",
            "input": inp, "cached": cached or 0, "output": out}


def aggregate(usages):
    quality = "measured" if usages and all(u["quality"] == "measured" for u in usages) else "partial"
    if not usages or all(u["quality"] == "unavailable" for u in usages):
        quality = "unavailable"
    return {"quality": quality, "basis": "api",
            **{key: sum(u[key] for u in usages) for key in
               ("input", "cached", "output")}}


def unresolved_tools(messages):
    """Never re-run an effect because the prior invocation died awaiting its result."""
    replied = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    pending = [call for m in messages if m.get("role") == "assistant"
               for call in m.get("tool_calls", []) if call["id"] not in replied]
    for call in pending:
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({
            "error": "Prior invocation ended before a result was durably captured. Outcome unknown; "
                     "inspect canonical receipts/state before any retry. This tool was NOT replayed."})})


def prompt(packet, activation, resumed):
    text = (packet["identity"] + "\n\nYou have Concorde MCP tools with input schemas; consult contract "
            "for further definitions/examples when needed. Persist useful understanding and unfinished "
            "work with mutate. Never modify brain.json or .concorde2 directly. At the end return only "
            'a JSON object with summary, such as {"summary":"what happened"}. Tool commits are durable. '
            "No hidden earlier activation history is part of this self. Current phase: " + packet["phase"]
            + ".\n\n" + encoded(packet).decode())
    if packet["phase"] == "rectification":
        text += ("\nThis is the distinct rectification turn. Work is over. Use MCP reads/mutations and "
                 "phase_complete. Read-only source inspection, including shell reads, is allowed; "
                 "do not edit products, run new product workloads or perform new external effects. "
                 "Reconcile concurrent/new evidence. Finish phase_complete before returning. Prior work "
                 "summary: " + activation.get("work_summary", ""))
        if not resumed:
            text += ("\nConversation unavailable: reconstruct only from durable graph, receipts, activation "
                     "history and logs. This is recovery, not proof earlier work completed.")
    if (activation.get("config", {}).get("work_reentry") and packet["phase"] == "rectification"
            and not activation.get("work_returns") and not activation.get("recovery_of")):
        text += ("\nOptional capability: before the original work-time boundary, resume_work may request "
                 "one separate return to work in this activation. This turn remains state-only. After "
                 "requesting, end with a summary without phase_complete; a new work turn and final "
                 "rectification follow within the same deadline. No return is required.")
    if packet["phase"] == "work" and activation.get("work_returns"):
        text += ("\nThis is the requested return to work, not rectification. Act within existing authority "
                 "and remaining original work time. Another distinct final rectification turn follows. "
                 "Your request: " + activation.get("work_return_reason", ""))
    return text


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API redirect refused")


class AdmissionWait(RuntimeError):
    def __init__(self, delay):
        super().__init__("broker admission throttled before upstream dispatch")
        self.delay = delay


def request_api(endpoint, token, body, deadline, activation, session):
    request = urllib.request.Request(endpoint, encoded(body), method="POST", headers={
        "Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {}),
        "X-Concorde-Deadline": dt.datetime.fromtimestamp(deadline, dt.timezone.utc).isoformat(),
        "X-Concorde-Activation": activation, "X-Concorde-Session": session})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=remaining(deadline)) as result:
            raw = result.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and exc.headers.get("X-Concorde-Upstream-Dispatched") == "false":
            try:
                delay = float(exc.headers.get("Retry-After", ""))
            except ValueError:
                delay = 0
            if 0 < delay <= 120:
                raise AdmissionWait(delay) from None
        # Do not echo provider bodies: unexpected providers can reflect credentials.
        raise RuntimeError(f"API HTTP {exc.code}; no automatic retry") from None
    except urllib.error.URLError:
        raise RuntimeError("API transport error; usage/outcome may be unknown; no automatic retry") from None
    if len(raw) > MAX_RESPONSE:
        raise ValueError("API response exceeds 8 MiB bound")
    return json.loads(raw)


def run(args, packet, transport=request_api):
    workspace = Path(os.environ["CONCORDE3_INSTANCE"]).resolve()
    activation_id = os.environ["CONCORDE3_ACTIVATION"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", activation_id):
        raise ValueError("unsafe activation identifier")
    if packet.get("activation") != activation_id or packet.get("phase") not in {"work", "rectification"}:
        raise ValueError("packet activation/phase mismatch")
    deadline = dt.datetime.fromisoformat(packet["deadline"].replace("Z", "+00:00")).timestamp()
    remaining(deadline)
    token = os.environ.get("CONCORDE3_MUSE_BROKER_TOKEN", "")
    # A credential-holding network sidecar may supply the bearer. No direct
    # provider key fallback exists, and normal deployments put no token here.
    rt = workspace / ".concorde2"
    # Read canonical runtime only for operational metadata absent from command Packet.
    state = json.loads((rt / "state.json").read_text())
    activation = state["activations"][activation_id]
    if activation.get("config", {}).get("mcp"):
        raise ValueError("additional configured MCP servers unsupported by this adapter; refusing silent omission")
    if args.workspace and not activation.get("config", {}).get("workspace"):
        raise ValueError("adapter workspace access requires instance workspace authority")
    if args.workspace and not activation.get("config", {}).get("external_sandbox"):
        raise ValueError("arbitrary shell requires an explicitly external-sandboxed instance")
    session = "muse-" + activation_id
    history = rt / "muse-sessions" / (session + ".json")
    saved = json.loads(history.read_text()) if history.exists() else None
    if saved and (saved.get("activation") != activation_id or saved.get("model") != args.model):
        raise ValueError("conversation identity/model mismatch")
    messages = saved["messages"] if saved else []
    unresolved_tools(messages)
    messages.append({"role": "user", "content": prompt(packet, activation, bool(saved))})
    turn = packet["phase"] + (".return" + str(activation["work_returns"]) if activation.get("work_returns") else "")
    logs = rt / "harness-logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = logs / f"{activation_id}.{turn}.jsonl"
    if log.exists():
        raise ValueError("phase log already exists; refusing untracked repeat invocation")
    append_log(log, {"type": "thread.started", "thread_id": session, "provider": "meta-api",
                     "model": args.model, "reasoning_effort": args.effort,
                     "reasoning_continuity": "visible_messages_only"})
    usages = []
    mcp = None
    request_pending = False

    def save():
        atomic(history, {"activation": activation_id, "model": args.model, "messages": messages})

    try:
        save()
        mcp = MCP(args.binary, workspace, activation_id, deadline)
        native = mcp.call("tools/list", {})["tools"]
        tools = [{"type": "function", "function": {"name": "concorde_" + tool["name"],
                  "description": tool["description"], "parameters": tool["inputSchema"]}} for tool in native]
        allowed = {tool["name"] for tool in native}
        if args.workspace:
            tools.append({"type": "function", "function": {"name": "workspace_shell", "description":
                "Execute a shell command in this isolated instance. Refresh Concorde context for the activity first. "
                "Never directly edit canonical runtime, daemonize, or escape process groups. During rectification "
                "only read-only source inspection is allowed; no new product effects or workloads.", "parameters": {
                    "type": "object", "properties": {"command": {"type": "string"},
                        "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 60}},
                    "required": ["command"], "additionalProperties": False}}})
        for number in range(args.max_requests):
            remaining(deadline)
            body = {"model": args.model, "messages": messages, "tools": tools,
                    "reasoning_effort": args.effort, "max_completion_tokens": args.max_output_tokens}
            request_ref = payload_record(log.with_suffix(f".api.{number:04d}.request.json"), body)
            append_log(log, {"type": "api.request.started", "request_number": number,
                             "request_ref": request_ref, "deadline": packet["deadline"]})
            request_pending = True
            for admission_attempt in range(20):
                try:
                    response = transport(args.endpoint, token, body, deadline, activation_id, session)
                    break
                except AdmissionWait as wait:
                    request_pending = False  # Broker certifies this attempt never reached provider.
                    append_log(log, {"type": "api.admission.wait", "request_number": number,
                                     "attempt": admission_attempt, "retry_after_seconds": wait.delay,
                                     "upstream_dispatched": False})
                    if wait.delay >= remaining(deadline) or admission_attempt == 19:
                        raise TimeoutError("broker admission wait exceeds phase deadline/retry bound") from None
                    time.sleep(wait.delay)
                    request_pending = True
            else:
                raise RuntimeError("broker admission retry bound reached")
            request_pending = False
            usage = parse_usage(response)
            usages.append(usage)
            response_ref = payload_record(log.with_suffix(f".api.{number:04d}.response.json"), response)
            append_log(log, {"type": "api.request.completed", "request_number": number,
                             "response_ref": response_ref, "usage": usage})
            choices = response.get("choices") or []
            if len(choices) != 1:
                raise ValueError("API must return exactly one completion")
            message = choices[0]["message"]
            if message.get("role") != "assistant":
                raise ValueError("API completion is not an assistant message")
            # Keep all message fields, including reasoning_content if actually supplied.
            messages.append(message)
            save()
            calls = message.get("tool_calls") or []
            if calls:
                for call in calls:
                    function = call.get("function", {})
                    name = function.get("name", "")
                    item = {"id": call["id"], "type": "mcp_tool_call" if name.startswith("concorde_")
                            else "command_execution", "tool": name.removeprefix("concorde_"),
                            "arguments": function.get("arguments"), "status": "in_progress"}
                    append_log(log, {"type": "item.started", "item": item})
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                        if not isinstance(arguments, dict):
                            raise ValueError("tool arguments must be an object")
                        if name == "workspace_shell" and args.workspace:
                            result = shell(arguments["command"], workspace, deadline,
                                           arguments.get("timeout_seconds", 60))
                            item.update(command=arguments["command"], exit_code=result["exit_code"],
                                        aggregated_output=result["output"], truncated=result["truncated"],
                                        timed_out=result["timed_out"])
                        elif name.startswith("concorde_") and name[9:] in allowed:
                            result = mcp.call("tools/call", {"name": name[9:], "arguments": arguments})
                        else:
                            raise ValueError("tool not advertised")
                        failed = result.get("isError") or result.get("timed_out") or result.get("exit_code", 0) != 0
                        item.update(result=result, status="failed" if failed else "completed")
                        if result.get("isError"):
                            item["error"] = encoded(result).decode()
                    except (ValueError, KeyError) as exc:
                        result = {"error": str(exc)}
                        item.update(error=str(exc), status="failed")
                    append_log(log, {"type": "item.completed", "item": item})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": encoded(result).decode()})
                    save()
                continue
            if choices[0].get("finish_reason") != "stop":
                raise ValueError("API turn did not stop normally; no manufactured successful summary")
            content = message.get("content")
            try:
                final = json.loads(content)
            except (ValueError, TypeError):
                raise ValueError("final response is not the required summary JSON") from None
            if not isinstance(final, dict) or set(final) != {"summary"} or not isinstance(final["summary"], str):
                raise ValueError("final response must contain exactly one string summary")
            outcome = {"summary": final["summary"], "session": session, "usage": aggregate(usages)}
            append_log(log, {"type": "turn.completed", "usage": {
                "input_tokens": outcome["usage"]["input"],
                "cached_input_tokens": outcome["usage"]["cached"],
                "output_tokens": outcome["usage"]["output"],
                "quality": outcome["usage"]["quality"], "basis": "api"}})
            atomic(log.with_suffix(".usage.json"), outcome["usage"])
            return outcome
        raise RuntimeError("per-phase API request limit reached")
    except BaseException as exc:
        partial = aggregate(usages + ([{"quality": "unavailable", "basis": "api", "input": 0,
                                       "cached": 0, "output": 0}] if request_pending else []))
        atomic(log.with_suffix(".usage.json"), partial)
        append_log(log, {"type": "error", "message": str(exc), "usage": partial})
        raise
    finally:
        if mcp:
            mcp.close()


def main():
    if "--concorde-capabilities" in sys.argv:
        print(json.dumps({"protocol": 2, "continuation": True}))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", choices=[MODEL], default=MODEL)
    parser.add_argument("--effort", choices=["minimal", "low", "medium", "high"], default="high")
    parser.add_argument("--binary", default="concorde3")
    parser.add_argument("--workspace", action="store_true")
    parser.add_argument("--max-requests", type=int, default=80)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 100 or not 1 <= args.max_output_tokens <= 8192:
        parser.error("request/output limits out of range")
    raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("packet exceeds 2 MiB")
    print(json.dumps(run(args, json.loads(raw))))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Muse harness failed: {error}", file=sys.stderr)
        sys.exit(1)

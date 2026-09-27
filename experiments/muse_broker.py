"""Opt-in, finite, fixed-provider experimental API relay; never used by local C3.

Only token charges are bounded. Reservations conservatively price a full context
at uncached rates plus the output ceiling. Missing/invalid usage retains that
reservation, including network failures. Restart does not refund uncertain calls.
No prompt, completion, provider error body or credential is written to the ledger.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import decimal
import fcntl
import hmac
import http.server
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

MODEL = "muse-spark-1.3-contributor"
ENDPOINT = "https://api.meta.ai/v1/chat/completions"
CONTEXT = 1_048_576
# Integer nanodollars/token: $0.10/M input, $0.002/M cached, $0.20/M output.
INPUT, CACHED, OUTPUT = 100, 2, 200
MAX_BODY = 8 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024


class Rejected(Exception):
    def __init__(self, status, code, retry_after=None):
        self.status, self.code, self.retry_after = status, code, retry_after


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    result = parsed.timestamp()
    if not math.isfinite(result):
        raise ValueError("invalid timestamp")
    return result


def dollars(value):
    number = decimal.Decimal(str(value)) * 1_000_000_000
    if not number.is_finite() or number <= 0 or number != number.to_integral():
        raise ValueError("invalid dollar cap")
    return int(number)


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".broker-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Ledger:
    def __init__(self, path, cap, stop_at, rpm=80, clock=time.time):
        self.path, self.cap, self.stop_at = Path(path), cap, stop_at
        self.rpm, self.clock = rpm, clock
        if not 1 <= rpm <= 80 or not math.isfinite(stop_at):
            raise ValueError("invalid finite broker limit")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.locked() as state:
            config = dict(model=MODEL, endpoint=ENDPOINT, cap_nanodollars=cap,
                          stop_at=stop_at, rpm=rpm,
                          prices_nanodollars=dict(input=INPUT, cached=CACHED, output=OUTPUT))
            if state and state.get("config") != config:
                raise ValueError("existing ledger configuration differs; cannot reset cap or expiry")
            if not state:
                state.update(schema_version=1, config=config, requests={})

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield state
            atomic_json(self.path, state)

    def reserve(self, max_output, metadata=None):
        now = self.clock()
        if now >= self.stop_at:
            raise Rejected(410, "experiment_expired")
        amount = CONTEXT * INPUT + max_output * OUTPUT
        with self.locked() as state:
            requests = state["requests"]
            recent = sorted(row["started_at"] for row in requests.values()
                            if row["started_at"] > now - 60)
            if len(recent) >= self.rpm:
                raise Rejected(429, "shared_rate_limit", max(1, math.ceil(recent[0] + 60 - now)))
            if sum(row["accounted_nanodollars"] for row in requests.values()) + amount > self.cap:
                raise Rejected(402, "shared_token_charge_cap")
            identity = uuid.uuid4().hex
            requests[identity] = dict(started_at=now, status="reserved",
                accounted_nanodollars=amount, reserved_nanodollars=amount,
                max_output=max_output, metadata=metadata or {})
        return identity

    def finish(self, identity, status, usage=None, provider_status=None, diagnostics=None):
        with self.locked() as state:
            row = state["requests"][identity]
            if row["status"] != "reserved":
                raise ValueError("request already finalized")
            row.update(status=status, finished_at=self.clock(), provider_status=provider_status)
            if diagnostics:
                row["provider_error"] = diagnostics
            normalized = validated_usage(usage, row["max_output"])
            if normalized is not None:
                row["usage"] = normalized
                # Preserve provider token counters (including reasoning) without
                # allowing unrecognized provider fields to become a prompt log.
                row["provider_usage"] = safe_usage_counters(usage)
                row["accounted_nanodollars"] = ((normalized["prompt_tokens"] - normalized["cached_tokens"]) * INPUT
                    + normalized["cached_tokens"] * CACHED + normalized["completion_tokens"] * OUTPUT)
                row["accounting"] = "validated_usage"
            else:
                row["accounting"] = "uncertain_reservation_retained"


def safe_usage_counters(usage):
    allowed = {"prompt_tokens", "completion_tokens", "total_tokens",
               "prompt_tokens_details", "completion_tokens_details"}
    result = {}
    for key in allowed & usage.keys():
        value = usage[key]
        if type(value) is int and value >= 0:
            result[key] = value
        elif isinstance(value, dict):
            result[key] = {name: count for name, count in value.items()
                           if re.fullmatch(r"[a-z_]{1,64}", name) and type(count) is int and count >= 0}
    return result


def validated_usage(usage, max_output):
    if not isinstance(usage, dict):
        return None
    prompt, output = usage.get("prompt_tokens"), usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        return None
    cached = details.get("cached_tokens", 0)
    if any(type(x) is not int for x in (prompt, output, cached)):
        return None
    if not (0 <= cached <= prompt <= CONTEXT and 0 <= output <= max_output):
        return None
    if "total_tokens" in usage and (type(usage["total_tokens"]) is not int or usage["total_tokens"] != prompt + output):
        return None
    return dict(prompt_tokens=prompt, cached_tokens=cached, completion_tokens=output)


def validate_request(body):
    if not isinstance(body, dict) or body.get("model") != MODEL:
        raise Rejected(400, "fixed_model_required")
    allowed = {"model", "messages", "tools", "tool_choice", "parallel_tool_calls",
               "reasoning_effort", "max_completion_tokens", "stream", "temperature", "top_p", "response_format"}
    if set(body) - allowed or body.get("stream", False) is not False:
        raise Rejected(400, "unsupported_request_option")
    maximum = body.get("max_completion_tokens")
    if type(maximum) is not int or not 1 <= maximum <= 8192:
        raise Rejected(400, "bounded_output_required")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise Rejected(400, "messages_required")
    for message in messages:
        if not isinstance(message, dict):
            raise Rejected(400, "invalid_message")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise Rejected(400, "text_only_experiment")
    tools = body.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools):
        raise Rejected(400, "function_tools_only")
    return maximum


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_diagnostics(raw, request, secrets_to_redact):
    """Keep a bounded error envelope, not provider-returned prompts or bodies.

    Messages are secondary diagnostics, never trusted instructions. Quoted
    payloads and exact request text are stripped in addition to both credentials.
    Unknown envelopes/non-JSON bodies are deliberately omitted.
    """
    try:
        decoded = json.loads(raw)
    except (ValueError, UnicodeError):
        return {}
    error = decoded.get("error") if isinstance(decoded, dict) else None
    if not isinstance(error, dict):
        return {}
    def redact(value):
        for secret in secrets_to_redact:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value
    result = {}
    for name in ("code", "type", "param"):
        value = error.get(name)
        if type(value) is int:
            value = str(value)
        if isinstance(value, str):
            value = redact(value)
            if len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9_.:/\[\]-]+", value):
                result[name] = value
    message = error.get("message")
    if isinstance(message, str):
        message = redact(message[:4096])
        # Quoted literals can be reflected field values or prompt excerpts.
        message = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`', "[quoted value omitted]", message)
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
        for value in strings(request):
            if len(value) >= 4:
                message = message.replace(value, "[request value omitted]")
        # Suppress messages that appear to embed a serialized request envelope.
        if re.search(r"\b(messages|reasoning_content|tool_calls|content)\s*[=:]", message):
            message = "Provider message omitted because it may reflect request content."
        result["message"] = " ".join(message.split())[:300]
    return result


def provider_call(body, key, timeout):
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        chunks, size = [], 0
        while True:
            # HTTPResponse closes fp immediately when read1 consumes the exact
            # Content-Length. A completed response needs no further socket read.
            if response.fp is None:
                if response.length not in (None, 0):
                    raise ValueError("truncated provider response")
                return response.status, b"".join(chunks)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("provider deadline")
            response.fp.raw._sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
            if not chunk:
                if response.length not in (None, 0):
                    raise ValueError("truncated provider response")
                return response.status, b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise ValueError("oversize provider response")


class BrokerServer(http.server.ThreadingHTTPServer):
    daemon_threads = False
    def __init__(self, address, ledger, key, token, forward=provider_call):
        self.ledger, self.key, self.token, self.forward = ledger, key, token, forward
        super().__init__(address, BrokerHandler)


class BrokerHandler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def reply(self, status, body, retry_after=None, upstream_dispatched=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        if upstream_dispatched is not None:
            self.send_header("X-Concorde-Upstream-Dispatched", "true" if upstream_dispatched else "false")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        identity = None
        try:
            if self.path != "/v1/chat/completions":
                raise Rejected(404, "fixed_endpoint_only")
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + self.server.token):
                raise Rejected(401, "invalid_experiment_token")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise Rejected(413, "bounded_request_required")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Rejected(400, "incomplete_request")
            body = json.loads(raw)
            maximum = validate_request(body)
            deadline = self.server.ledger.stop_at
            if self.headers.get("X-Concorde-Deadline"):
                deadline = min(deadline, timestamp(self.headers["X-Concorde-Deadline"]))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise Rejected(410, "request_deadline_expired")
            metadata = {}
            for name in ("Activation", "Session"):
                value = self.headers.get("X-Concorde-" + name, "")
                if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
                    metadata[name.lower()] = value
            identity = self.server.ledger.reserve(maximum, metadata)
            status, result = self.server.forward(raw, self.server.key, min(remaining, 120))
            decoded = json.loads(result)
            if not isinstance(decoded, dict):
                raise ValueError("invalid response")
            self.server.ledger.finish(identity, "completed", decoded.get("usage"), status)
            identity = None
            self.reply(status, decoded)
        except Rejected as error:
            self.reply(error.status, {"error": {"code": error.code}}, error.retry_after,
                       upstream_dispatched=False)
        except urllib.error.HTTPError as error:
            diagnostics = {}
            with contextlib.suppress(Exception):
                # Never persist or forward the raw provider body. Error reads
                # are bounded and are not a second request or automatic retry.
                if error.fp is not None and getattr(error.fp, "fp", None) is not None:
                    error.fp.fp.raw._sock.settimeout(2)
                raw_error = error.read(65537)
                if len(raw_error) <= 65536:
                    diagnostics = provider_diagnostics(raw_error, body, (self.server.key, self.server.token))
            error.close()
            if identity:
                self.server.ledger.finish(identity, "provider_error", provider_status=error.code, diagnostics=diagnostics)
                identity = None
            self.reply(error.code if 400 <= error.code <= 599 else 502,
                       {"error": {"code": "provider_error", "provider_status": error.code,
                                  **({"diagnostics": diagnostics} if diagnostics else {})}},
                       5 if error.code == 429 else None, upstream_dispatched=True)
        except (ValueError, TypeError, OverflowError):
            if identity:
                self.server.ledger.finish(identity, "invalid_provider_response")
                identity = None
                self.reply(502, {"error": {"code": "invalid_provider_response"}})
            else:
                self.reply(400, {"error": {"code": "invalid_request"}})
        except (OSError, TimeoutError):
            if identity:
                self.server.ledger.finish(identity, "transport_uncertain")
                identity = None
            with contextlib.suppress(OSError):
                self.reply(502, {"error": {"code": "transport_uncertain"}})
        except Exception:
            if identity:
                self.server.ledger.finish(identity, "unexpected_failure")
                identity = None
            with contextlib.suppress(OSError):
                self.reply(502, {"error": {"code": "broker_failure_uncertain"}})
        finally:
            if identity:
                # Unexpected faults remain reserved even if final logging fails.
                with contextlib.suppress(Exception):
                    self.server.ledger.finish(identity, "unexpected_failure")


def read_key(env_file=None):
    if env_file:
        for line in Path(env_file).read_text().splitlines():
            match = re.match(r"(?:export\s+)?META_API_KEY\s*=\s*(.*)$", line.strip())
            if match:
                value = match[1].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value:
                    return value
        raise ValueError("META_API_KEY is absent")
    value = os.environ.get("META_API_KEY")
    if not value:
        raise ValueError("META_API_KEY is absent")
    return value


def create_token_if_missing(path):
    """Provision one owner-only runtime credential; never replace or print it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as stream:
        stream.write(secrets.token_urlsafe(48) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--create-token-if-missing", action="store_true")
    parser.add_argument("--key-env-file", type=Path)
    parser.add_argument("--stop-at", required=True)
    parser.add_argument("--cap-usd", default="50")
    parser.add_argument("--rpm", type=int, default=80)
    args = parser.parse_args()
    address = ipaddress.ip_address(args.listen)
    if address.is_unspecified or not (address.is_loopback or address.is_private) or address.version != 4:
        parser.error("listen must be an explicit loopback/private IPv4 address, never wildcard/public")
    stop = timestamp(args.stop_at)
    if not time.time() < stop <= time.time() + 4 * 3600:
        parser.error("initial stop must be within four hours")
    if args.create_token_if_missing:
        create_token_if_missing(args.token_file)
    token = args.token_file.read_text().strip()
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        parser.error("token file must contain a strong scoped bearer")
    ledger = Ledger(args.ledger, dollars(args.cap_usd), stop, args.rpm)
    server = BrokerServer((args.listen, args.port), ledger, read_key(args.key_env_file), token)
    def expire():
        time.sleep(max(0, stop - time.time()))
        server.shutdown()
    threading.Thread(target=expire, daemon=True).start()
    print(json.dumps({"broker": "ready", "model": MODEL, "listen": args.listen,
                      "port": args.port, "stop_at": stop, "cap_usd": args.cap_usd}), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

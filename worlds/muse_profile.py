"""Explicit experimental API embodiment; no fallback from subscription worlds."""
import hashlib
import datetime as dt
import json
import math
from pathlib import Path
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

from host_runtime import save

MODEL = "muse-spark-1.3-contributor"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def profile(config):
    api = config.get("api_harness")
    if api is None:
        return None
    if config.get("model") != MODEL or config.get("effort") != "high":
        raise ValueError("explicit Muse Contributor/high profile required")
    if not isinstance(api, dict) or set(api) != {"endpoint", "token_file", "adapter"}:
        raise ValueError("explicit endpoint, token_file and adapter required")
    url = urlsplit(api["endpoint"])
    if url.scheme != "http" or url.hostname not in ("172.17.0.1", "127.0.0.1") or url.path != "/v1/chat/completions" or url.username or url.password or url.query or url.fragment:
        raise ValueError("local fixed broker endpoint required")
    for key in ("token_file", "adapter"):
        if not Path(api[key]).is_absolute() or not Path(api[key]).is_file():
            raise ValueError("existing absolute API asset required: " + key)
    return api


def command(proxy):
    return ["python3", "/world/muse_meter.py", "--endpoint", f"http://{proxy}:8080/v1/chat/completions",
            "--model", MODEL, "--effort", "high", "--binary", "/usr/local/bin/concorde3", "--workspace"]


class MuseDriver:
    """Counterpart decisions retain the same bounded World action interpreter."""
    def __init__(self, world):
        self.world = world
        with world.s.transaction() as db:
            self.api = profile(world.s.meta(db, "config"))
        if self.api is None:
            raise ValueError("API driver cannot replace an undeclared subscription profile")

    def __call__(self, actor, prompt, category="counterpart"):
        from .driver import SCHEMA
        from . import budget
        permit = self.world.reserve_call(actor, category, 240)
        identity = permit["id"]
        root = self.world.s.root / "calls" / identity
        root.mkdir(parents=True, mode=0o700)
        save(root / "prompt.txt", prompt)
        save(root / "provenance.json", {"model": MODEL, "effort": "high", "category": category,
             "actor": actor, "subscription": False, "transport": "explicit_meta_api_broker",
             "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()})
        usage, success = None, False
        try:
            with self.world.s.transaction() as db:
                cfg = self.world.s.meta(db, "config")
            budget.reserve(identity, concurrency=cfg.get("shared_concurrency", 8))
            body = {"model": MODEL, "reasoning_effort": "high", "max_completion_tokens": 8192,
                    "messages": [{"role": "system", "content": "Return only a JSON object conforming to this schema: " + json.dumps(SCHEMA)},
                                 {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
            req = urllib.request.Request(self.api["endpoint"], data=json.dumps(body).encode(), headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + Path(self.api["token_file"]).read_text().strip(),
                "X-Concorde-Deadline": dt.datetime.fromtimestamp(permit["deadline"], dt.timezone.utc).isoformat(), "X-Concorde-Activation": identity,
                "X-Concorde-Session": "counterpart-" + actor})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            self.world.finish_call(identity, "running", {"transport": "explicit_meta_api_broker"})
            admission_waits = []
            for attempt in range(20):
                remaining = permit["deadline"] - time.time()
                if remaining <= 0:
                    raise TimeoutError("counterpart request deadline expired before dispatch")
                try:
                    with opener.open(req, timeout=remaining) as response:
                        content = response.read(8 * 1024 * 1024 + 1)
                        if len(content) > 8 * 1024 * 1024:
                            raise ValueError("bounded API response exceeded")
                        raw = json.loads(content)
                    break
                except urllib.error.HTTPError as error:
                    if error.code != 429 or error.headers.get("X-Concorde-Upstream-Dispatched") != "false":
                        raise
                    try:
                        delay = float(error.headers.get("Retry-After", ""))
                    except ValueError:
                        raise error
                    if not math.isfinite(delay) or not 0 < delay <= 120:
                        raise
                    admission_waits.append({"at": time.time(), "attempt": attempt,
                        "retry_after_seconds": delay, "upstream_dispatched": False,
                        "reservation": identity, "deadline": permit["deadline"]})
                    save(root / "admission-waits.json", admission_waits)
                    error.close()
                    if delay >= permit["deadline"] - time.time() or attempt == 19:
                        raise TimeoutError("counterpart broker wait exceeds original deadline/retry bound") from None
                    time.sleep(delay)
            else:
                raise RuntimeError("counterpart broker admission retry bound reached")
            save(root / "api-response.json", raw)
            u = raw.get("usage", {})
            details = u.get("prompt_tokens_details") or {}
            values = {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens"),
                      "cached_input_tokens": details.get("cached_tokens") if isinstance(details, dict) else None}
            if all(type(v) is int and v >= 0 for v in values.values()) and values["cached_input_tokens"] <= values["input_tokens"]:
                usage = [values]
            result = json.loads(raw["choices"][0]["message"]["content"])
            if not isinstance(result, dict) or not isinstance(result.get("actions"), list) or type(result.get("finish")) is not bool:
                raise ValueError("invalid counterpart decision envelope")
            save(root / "response.json", {"result": result, "actual_token_usage": usage, "usage_basis": "api"})
            success = True
            return result
        finally:
            self.world.finish_call(identity, "completed" if success else "failed", {
                "response": str(root / "response.json"), "usage_basis": "api", "actual_token_usage": usage,
                "limitation": "Missing/failed dispatched usage remains reserved by the broker. Only explicit pre-dispatch rate denial may retry within the same reservation/deadline."})

"""Mechanical protocol tests only; scripted responses are not behavioral evidence."""
import argparse
import contextlib
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from harnesses import muse

ROOT = Path(__file__).resolve().parents[1]


def response(content=None, calls=None, finish="stop", cached=90):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"id": "fake-response", "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "prompt_tokens_details": {"cached_tokens": cached},
                      "completion_tokens_details": {"reasoning_tokens": 4}}}


def call(name, arguments, identity):
    return {"id": identity, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}


class Units(unittest.TestCase):
    def test_usage_measured_and_reasoning_not_double_counted(self):
        self.assertEqual(muse.parse_usage(response()), {
            "quality": "measured", "basis": "api", "input": 100, "cached": 90, "output": 10})

    def test_missing_cached_is_partial_and_bad_usage_not_zero_measured(self):
        value = response()
        del value["usage"]["prompt_tokens_details"]
        self.assertEqual(muse.parse_usage(value)["quality"], "partial")
        self.assertEqual(muse.parse_usage({})["quality"], "unavailable")
        for cached in (-1, True, 101, "9"):
            self.assertEqual(muse.parse_usage(response(cached=cached))["quality"], "unavailable")
        for details in ([], "malformed"):
            value = response()
            value["usage"]["prompt_tokens_details"] = details
            self.assertEqual(muse.parse_usage(value)["quality"], "unavailable")
        value = response()
        value["usage"]["total_tokens"] = 101
        self.assertEqual(muse.parse_usage(value)["quality"], "unavailable")
        self.assertEqual(muse.aggregate([muse.parse_usage(response()), muse.parse_usage({})]), {
            "quality": "partial", "basis": "api", "input": 100, "cached": 90, "output": 10})

    def test_pending_tools_are_not_reexecuted(self):
        messages = [{"role": "assistant", "tool_calls": [call("concorde_effect", {}, "uncertain")]}]
        muse.unresolved_tools(messages)
        self.assertEqual(messages[-1]["tool_call_id"], "uncertain")
        self.assertIn("NOT replayed", messages[-1]["content"])
        muse.unresolved_tools(messages)
        self.assertEqual(len(messages), 2)

    def test_shell_output_bound_and_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = muse.shell("printf abcdef", temporary, time.time() + 5, cap=4)
            self.assertEqual(result["output"], "abcd")
            self.assertTrue(result["truncated"])
            result = muse.shell("sleep 5", temporary, time.time() + 2, timeout=1)
            self.assertTrue(result["timed_out"])

    def test_shell_never_inherits_provider_or_broker_credentials(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
                "META_API_KEY": "SYNTHETIC_META_SENTINEL", "MODEL_API_KEY": "SYNTHETIC_MODEL_SENTINEL",
                "CONCORDE3_MUSE_BROKER_TOKEN": "SYNTHETIC_BROKER_SENTINEL"}):
            result = muse.shell("env", temporary, time.time() + 5)
            self.assertNotIn("SENTINEL", result["output"])

    def test_preflight_needs_no_credentials_or_packet(self):
        result = subprocess.run([sys.executable, str(ROOT / "harnesses/muse.py"),
                                 "--concorde-capabilities"], capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"protocol": 2, "continuation": True})


class NativeLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = str(Path(cls.build.name) / "concorde3")
        subprocess.run(["go", "build", "-o", cls.binary, "./cmd/concorde3"], cwd=ROOT,
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def cli(self, *arguments, check=True):
        return subprocess.run([self.binary, *map(str, arguments)], capture_output=True,
                              text=True, check=check, timeout=45)

    @contextlib.contextmanager
    def fake_api(self, responder):
        requests = []
        errors = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append((body, dict(self.headers)))
                    reply = responder(body)
                    status, headers = 200, {}
                    if isinstance(reply, tuple):
                        status, headers, reply = reply
                    data = json.dumps(reply).encode()
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as exc:
                    errors.append(str(exc))
                    self.send_error(500)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions", requests, errors
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def prepare(self, instance, endpoint):
        self.cli("init", "--workspace", instance)
        self.cli("configure", instance, json.dumps({"harness": "command", "model": muse.MODEL,
                 "effort": "high", "external_sandbox": True, "deadline_seconds": 30,
                 "command": [sys.executable, str(ROOT / "harnesses/muse.py"), "--endpoint", endpoint,
                             "--binary", self.binary, "--workspace"]}))
        self.cli("resume", instance)

    def test_real_mcp_writeback_separate_rectification_session_and_restart(self):
        stages = {"work": 0, "rectification": 0}

        def responder(body):
            for tool in body["tools"]:
                parameters = tool["function"]["parameters"]
                if "required" in parameters:
                    self.assertIsInstance(parameters["required"], list,
                                          "API JSON Schema required must be an array, never null")
            messages = body["messages"]
            current = [m for m in messages if m["role"] == "user"][-1]["content"]
            phase = "rectification" if "Current phase: rectification." in current else "work"
            stage = stages[phase]
            stages[phase] += 1
            if phase == "work":
                if stage == 0:
                    return response(calls=[call("concorde_mutate", {"reason": "mechanical test",
                        "changes": {"items": [{"expected_revision": 0, "item": {
                            "id": "adapter-proof", "node": "undertaking", "kind": "observation",
                            "text": "Durable native MCP writeback", "status": "active"}}]}}, "write")],
                        finish="tool_calls")
                return response('{"summary":"Work durably recorded"}')
            if stage == 0:
                self.assertTrue(any(m.get("content") == '{"summary":"Work durably recorded"}' for m in messages))
                self.assertIn("distinct rectification turn", current)
                return response(calls=[call("concorde_state", {"section": "config"}, "state")], finish="tool_calls")
            if stage == 1:
                outer = json.loads(messages[-1]["content"])
                state = json.loads(outer["content"][0]["text"])
                return response(calls=[call("concorde_phase_complete", {"completion": {
                    "expected_seq": state["sequence"], "considered": ["adapter-proof"],
                    "continuation": "wait", "coverage": "No external duties in mechanical test",
                    "reason": "Native loop mechanical proof"}}, "complete")], finish="tool_calls")
            return response('{"summary":"Rectified through native MCP"}')

        with tempfile.TemporaryDirectory() as temporary, self.fake_api(responder) as (endpoint, requests, errors):
            instance = Path(temporary) / "instance"
            self.prepare(instance, endpoint)
            result = self.cli("pulse", instance, check=False)
            self.assertEqual(errors, [])
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads((instance / ".concorde2/state.json").read_text())
            activation = next(iter(state["activations"].values()))
            self.assertEqual(activation["status"], "completed")
            self.assertEqual(activation["usage"], {"quality": "measured", "basis": "api",
                             "input": 500, "output": 50, "cached": 450})
            self.assertEqual(activation["session"], "muse-" + activation["id"])
            self.assertEqual(stages, {"work": 2, "rectification": 3})
            restored = json.loads(self.cli("call", instance, "item", '{"id":"adapter-proof"}').stdout)
            self.assertEqual(restored["text"], "Durable native MCP writeback")
            for body, headers in requests:
                self.assertEqual(body["model"], muse.MODEL)
                self.assertIn("X-Concorde-Deadline", headers)
                self.assertNotIn("Authorization", headers)
                self.assertIn("workspace_shell", [t["function"]["name"] for t in body["tools"]])
            self.assertEqual(len(list((instance / ".concorde2/harness-logs").glob("*.jsonl"))), 2)
            self.assertEqual(len(list((instance / ".concorde2/muse-sessions").glob("*.json"))), 1)
            logs = [json.loads(line) for path in (instance / ".concorde2/harness-logs").glob("*.jsonl")
                    for line in path.read_text().splitlines()]
            completed = [event for event in logs if event["type"] == "turn.completed"]
            self.assertEqual(sum(event["usage"]["input_tokens"] for event in completed), 500)
            mcp_calls = [event["item"] for event in logs if event["type"] == "item.completed"
                         and event["item"]["type"] == "mcp_tool_call"]
            self.assertEqual({item["tool"] for item in mcp_calls}, {"mutate", "state", "phase_complete"})
            for event in logs:
                for key in ("request_ref", "response_ref"):
                    if key in event:
                        record = event[key]
                        data = (instance / ".concorde2/harness-logs" / record["path"]).read_bytes()
                        self.assertEqual(len(data), record["bytes"])
                        self.assertEqual(muse.hashlib.sha256(data).hexdigest(), record["sha256"])

    def test_summary_cannot_manufacture_phase_completion(self):
        with tempfile.TemporaryDirectory() as temporary, self.fake_api(
                lambda body: response('{"summary":"I claim everything is complete"}')) as (endpoint, requests, errors):
            instance = Path(temporary) / "instance"
            self.prepare(instance, endpoint)
            self.cli("pulse", instance, check=False)
            state = json.loads((instance / ".concorde2/state.json").read_text())
            activation = next(iter(state["activations"].values()))
            self.assertNotEqual(activation["status"], "completed")
            self.assertIn("rectification", activation["phase"])
            self.assertEqual(len(requests), 2)
            self.assertEqual(errors, [])

    def test_truncation_and_malformed_final_are_failed_not_retried(self):
        for reply in (response("not json"), response('{"summary":"partial"}', finish="length")):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as temporary, self.fake_api(
                    lambda body: reply) as (endpoint, requests, errors):
                instance = Path(temporary) / "instance"
                self.prepare(instance, endpoint)
                self.cli("pulse", instance, check=False)
                state = json.loads((instance / ".concorde2/state.json").read_text())
                activation = next(iter(state["activations"].values()))
                self.assertNotEqual(activation["status"], "completed")
                self.assertEqual(len(requests), 2)  # work then explicit rectification; no API retry
                partials = list((instance / ".concorde2/harness-logs").glob("*.usage.json"))
                self.assertEqual(len(partials), 2)
                self.assertTrue(all(json.loads(p.read_text())["quality"] == "measured" for p in partials))

    def test_only_certified_undispatched_throttling_is_retried(self):
        count = 0

        def responder(body):
            nonlocal count
            count += 1
            if count == 1:
                return 429, {"X-Concorde-Upstream-Dispatched": "false", "Retry-After": "0.01"}, {}
            return response('{"summary":"No native completion claimed"}')

        with tempfile.TemporaryDirectory() as temporary, self.fake_api(responder) as (endpoint, requests, errors):
            instance = Path(temporary) / "instance"
            self.prepare(instance, endpoint)
            self.cli("pulse", instance, check=False)
            self.assertEqual(len(requests), 3)  # initial admission denied + work response + rectification
            logs = [json.loads(line) for path in (instance / ".concorde2/harness-logs").glob("*.jsonl")
                    for line in path.read_text().splitlines()]
            self.assertEqual(sum(event["type"] == "api.admission.wait" for event in logs), 1)
            self.assertFalse(any(event["type"] == "error" for event in logs))

        # Provider errors (even 429) may have incurred usage and are not auto-retried.
        with tempfile.TemporaryDirectory() as temporary, self.fake_api(lambda body: (
                429, {"X-Concorde-Upstream-Dispatched": "true", "Retry-After": "0.01"},
                {"error": "SYNTHETIC_PROVIDER_SECRET"})) as (endpoint, requests, errors):
            instance = Path(temporary) / "instance"
            self.prepare(instance, endpoint)
            self.cli("pulse", instance, check=False)
            self.assertEqual(len(requests), 2)  # work failed then distinct rectification; no retry
            logs = "\n".join(path.read_text() for path in (instance / ".concorde2/harness-logs").iterdir())
            self.assertNotIn("SYNTHETIC_PROVIDER_SECRET", logs)
            self.assertNotIn('"type":"api.admission.wait"', logs)

    def test_undeclared_other_mcp_cannot_be_silently_omitted(self):
        with tempfile.TemporaryDirectory() as temporary, self.fake_api(lambda body: response(
                '{"summary":"unused"}')) as (endpoint, requests, errors):
            instance = Path(temporary) / "instance"
            self.prepare(instance, endpoint)
            self.cli("configure", instance, json.dumps({"mcp": {"other": {
                "command": ["false"], "approval": "approve"}}}))
            self.cli("pulse", instance, check=False)
            self.assertEqual(requests, [])

    def test_past_deadline_refuses_before_api_request(self):
        args = argparse.Namespace(endpoint="unused", model=muse.MODEL, effort="high", binary=self.binary,
                                  workspace=False, max_requests=80, max_output_tokens=8192)
        packet = {"activation": "act.test", "phase": "work", "deadline": "2000-01-01T00:00:00Z"}
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
                "CONCORDE3_INSTANCE": temporary, "CONCORDE3_ACTIVATION": "act.test"}):
            with self.assertRaises(TimeoutError):
                muse.run(args, packet, transport=lambda *a: self.fail("should not dispatch"))

    def test_shell_trace_qualifies_actual_output_not_named_marker(self):
        from evals.adaptation_observe import exposed_output
        stages = 0

        def responder(body):
            nonlocal stages
            stages += 1
            if stages == 1:
                return response(calls=[call("workspace_shell", {
                    "command": "printf 'customer-feedback-marker'; : argument-only-marker"}, "shell-read")],
                    finish="tool_calls")
            return response('{"summary":"Mechanical shell trace"}')

        with tempfile.TemporaryDirectory() as temporary, self.fake_api(responder) as (endpoint, requests, errors):
            instance = Path(temporary) / "instance"
            self.prepare(instance, endpoint)
            self.cli("pulse", instance, check=False)
            events = [json.loads(line) for path in (instance / ".concorde2/harness-logs").glob("*.jsonl")
                      for line in path.read_text().splitlines()]
            shell_event = next(event for event in events if event["type"] == "item.completed"
                               and event["item"]["type"] == "command_execution")
            self.assertTrue(exposed_output(shell_event, "customer-feedback-marker"))
            self.assertFalse(exposed_output(shell_event, "argument-only-marker"))
            self.assertEqual(shell_event["item"]["exit_code"], 0)
            self.assertEqual(shell_event["item"]["aggregated_output"], "customer-feedback-marker")
            self.assertEqual(shell_event["item"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()

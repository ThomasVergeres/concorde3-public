import concurrent.futures
import http.server
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from io import BytesIO

from experiments.muse_broker import (BrokerServer, CONTEXT, INPUT, OUTPUT, Ledger,
    MODEL, Rejected, create_token_if_missing, dollars, provider_call, provider_diagnostics, read_key, validate_request, validated_usage)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = lambda: 1000.
        self.ledger = Ledger(self.root / "ledger.json", dollars("50"), 2000, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return json.loads(self.ledger.path.read_text())

    def test_atomic_concurrent_reservations_cannot_overspend(self):
        cap = (CONTEXT * INPUT + 8192 * OUTPUT) * 3
        other = Ledger(self.root / "small.json", cap, 2000, clock=self.clock)
        def attempt(_):
            try:
                return other.reserve(8192)
            except Rejected as error:
                self.assertEqual(error.code, "shared_token_charge_cap")
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            accepted = list(pool.map(attempt, range(40)))
        self.assertEqual(sum(value is not None for value in accepted), 3)
        self.assertEqual(sum(row["accounted_nanodollars"] for row in json.loads(other.path.read_text())["requests"].values()), cap)

    def test_valid_usage_releases_reservation_and_double_finish_rejected(self):
        identity = self.ledger.reserve(8192)
        self.ledger.finish(identity, "completed", {"prompt_tokens": 1000,
            "completion_tokens": 100, "total_tokens": 1100,
            "prompt_tokens_details": {"cached_tokens": 800}})
        row = self.state()["requests"][identity]
        self.assertEqual(row["accounted_nanodollars"], 41600)
        with self.assertRaises(ValueError):
            self.ledger.finish(identity, "completed", {})

    def test_unknown_kept_through_restart_config_cannot_reset(self):
        identity = self.ledger.reserve(8192)
        self.ledger.finish(identity, "transport_uncertain")
        restart = Ledger(self.ledger.path, dollars("50"), 2000, clock=self.clock)
        with restart.locked() as state:
            row = state["requests"][identity]
            self.assertEqual(row["accounted_nanodollars"], row["reserved_nanodollars"])
        with self.assertRaises(ValueError):
            Ledger(self.ledger.path, dollars("100"), 2000)
        with self.assertRaises(ValueError):
            Ledger(self.ledger.path, dollars("50"), 3000)

    def test_inflight_reservation_survives_restart(self):
        identity = self.ledger.reserve(8192)
        restart = Ledger(self.ledger.path, dollars("50"), 2000, clock=self.clock)
        with restart.locked() as state:
            self.assertEqual(state["requests"][identity]["status"], "reserved")

    def test_rate_and_expiry(self):
        ledger = Ledger(self.root / "rate.json", dollars("50"), 2000, rpm=1, clock=self.clock)
        ledger.reserve(8192)
        with self.assertRaises(Rejected) as caught:
            ledger.reserve(8192)
        self.assertEqual(caught.exception.status, 429)
        ledger.clock = lambda: 2000
        with self.assertRaises(Rejected) as caught:
            ledger.reserve(8192)
        self.assertEqual(caught.exception.status, 410)

    def test_usage_invalid_never_refunds(self):
        for usage in ({}, {"prompt_tokens": True, "completion_tokens": 0},
            {"prompt_tokens": 5, "completion_tokens": 9000},
            {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 2},
            {"prompt_tokens": 5, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 8}}):
            self.assertIsNone(validated_usage(usage, 8192))

    def test_request_restricts_routes_pricing_and_output(self):
        good = {"model": MODEL, "messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 8192}
        self.assertEqual(validate_request(good), 8192)
        for update in ({"model": "other"}, {"stream": True}, {"max_completion_tokens": 8193},
            {"tools": [{"type": "web_search"}]}, {"arbitrary_url": "http://other"},
            {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}):
            with self.assertRaises(Rejected):
                validate_request(dict(good, **update))

    def test_only_selected_env_key(self):
        path = self.root / ".env"
        path.write_text('UNRELATED=do-not-use\nMETA_API_KEY="test-secret"\nOTHER=x\n')
        self.assertEqual(read_key(path), "test-secret")

    def test_token_bootstrap_is_private_and_preserves_existing(self):
        path = self.root / "runtime" / "token"
        self.assertTrue(create_token_if_missing(path))
        before = path.read_bytes()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertGreaterEqual(len(before.strip()), 32)
        self.assertFalse(create_token_if_missing(path))
        self.assertEqual(path.read_bytes(), before)

    def test_token_bootstrap_does_not_follow_existing_symlink(self):
        original = self.root / "original"
        original.write_text("preserved")
        link = self.root / "link"
        link.symlink_to(original)
        self.assertFalse(create_token_if_missing(link))
        self.assertEqual(original.read_text(), "preserved")

    def test_http_end_to_end_no_secret_prompt_in_ledger(self):
        token = "scoped-token-not-real-api-key"
        ledger = Ledger(self.root / "http.json", dollars("50"), time.time() + 60)
        calls = []
        def forward(body, key, timeout):
            calls.append((json.loads(body), key, timeout))
            return 200, json.dumps({"choices": [{"message": {"role": "assistant", "content": "private-result"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}).encode()
        server = BrokerServer(("127.0.0.1", 0), ledger, "real-secret-only-on-host", token, forward)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        def call(path="/v1/chat/completions", auth=token, deadline=None):
            headers = {"Authorization": "Bearer " + auth, "Content-Type": "application/json"}
            if deadline:
                headers["X-Concorde-Deadline"] = deadline
            body = {"model": MODEL, "messages": [{"role": "user", "content": "private-prompt"}], "max_completion_tokens": 8192}
            request = urllib.request.Request(base + path, json.dumps(body).encode(), headers)
            return urllib.request.urlopen(request)
        with call() as response:
            self.assertIn(b"private-result", response.read())
        self.assertEqual(calls[0][1], "real-secret-only-on-host")
        for path, auth, deadline, expected in (("/v1/models", token, None, 404),
                ("/v1/chat/completions", "bad", None, 401),
                ("/v1/chat/completions", token, "2000-01-01T00:00:00Z", 410)):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                call(path, auth, deadline)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(caught.exception.headers["X-Concorde-Upstream-Dispatched"], "false")
        self.assertEqual(len(calls), 1)
        saved = ledger.path.read_text()
        for secret in ("private-prompt", "private-result", "real-secret-only-on-host", token):
            self.assertNotIn(secret, saved)

    def test_provider_error_body_not_logged_and_reservation_retained(self):
        ledger = Ledger(self.root / "error.json", dollars("50"), time.time() + 60)
        def forward(body, key, timeout):
            raise urllib.error.HTTPError("https://fixed", 429, "secret-detail", {}, BytesIO(b"private provider error"))
        server = BrokerServer(("127.0.0.1", 0), ledger, "real-secret", "scoped", forward)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            body = {"model": MODEL, "messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 8192}
            request = urllib.request.Request("http://127.0.0.1:" + str(server.server_port) + "/v1/chat/completions",
                json.dumps(body).encode(), {"Authorization": "Bearer scoped"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 429)
            self.assertEqual(caught.exception.headers["X-Concorde-Upstream-Dispatched"], "true")
            self.assertNotIn(b"private provider error", caught.exception.read())
            state = json.loads(ledger.path.read_text())
            row = next(iter(state["requests"].values()))
            self.assertEqual(row["provider_status"], 429)
            self.assertEqual(row["accounted_nanodollars"], row["reserved_nanodollars"])
            self.assertNotIn("secret-detail", ledger.path.read_text())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_error_diagnostics_bounded_and_sensitive_fields_excluded(self):
        raw = json.dumps({"error": {"code": "invalid_schema", "type": "invalid_request_error",
            "param": "tools[0].function.parameters.required", "message":
                'Invalid schema: expected array but got null. key=real-secret token=scoped-secret private-prompt "private fragment"',
            "content": "private reflected request", "request": {"messages": ["private-prompt"]}}})
        got = provider_diagnostics(raw, {"messages": [{"content": "private-prompt"}]}, ("real-secret", "scoped-secret"))
        self.assertEqual(got["param"], "tools[0].function.parameters.required")
        self.assertIn("expected array but got null", got["message"])
        self.assertLessEqual(len(got["message"]), 300)
        for hidden in ("real-secret", "scoped-secret", "private-prompt", "private fragment", "private reflected request"):
            self.assertNotIn(hidden, json.dumps(got))
        self.assertEqual(provider_diagnostics(b"private plain-text body", {}, ()), {})

    def test_provider_transport_complete_body_socket_closure(self):
        # Real local sockets reproduce urllib closing response.fp as the last
        # read1 consumes a Content-Length or chunked response. No paid API call.
        payload = b'{"usage":{"prompt_tokens":10,"completion_tokens":2}}'
        for framing in ("length", "chunked", "eof", "truncated"):
            with self.subTest(framing=framing):
                class Handler(http.server.BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.1"
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers["Content-Length"]))
                        self.send_response(200)
                        if framing in ("length", "truncated"):
                            self.send_header("Content-Length", str(len(payload) + (3 if framing == "truncated" else 0)))
                        elif framing == "chunked":
                            self.send_header("Transfer-Encoding", "chunked")
                        else:
                            self.send_header("Connection", "close")
                        self.end_headers()
                        if framing == "chunked":
                            self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n0\r\n\r\n")
                        else:
                            self.wfile.write(payload)
                        self.wfile.flush()
                        self.close_connection = True
                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    endpoint = "http://127.0.0.1:" + str(server.server_port) + "/v1/chat/completions"
                    with mock.patch("experiments.muse_broker.ENDPOINT", endpoint):
                        if framing == "truncated":
                            with self.assertRaisesRegex(ValueError, "truncated provider response"):
                                provider_call(b"{}", "local-synthetic-key", 5)
                        else:
                            self.assertEqual(provider_call(b"{}", "local-synthetic-key", 5), (200, payload))
                finally:
                    server.shutdown()
                    thread.join()
                    server.server_close()

    def test_unexpected_transport_bug_returns_sanitized_failure(self):
        ledger = Ledger(self.root / "unexpected.json", dollars("50"), time.time() + 60)
        def forward(body, key, timeout):
            raise AttributeError("private internal detail")
        server = BrokerServer(("127.0.0.1", 0), ledger, "real-secret", "scoped", forward)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            body = {"model": MODEL, "messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 8192}
            request = urllib.request.Request("http://127.0.0.1:" + str(server.server_port) + "/v1/chat/completions",
                json.dumps(body).encode(), {"Authorization": "Bearer scoped"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 502)
            self.assertEqual(json.loads(caught.exception.read())["error"]["code"], "broker_failure_uncertain")
            row = next(iter(json.loads(ledger.path.read_text())["requests"].values()))
            self.assertEqual(row["status"], "unexpected_failure")
            self.assertEqual(row["accounted_nanodollars"], row["reserved_nanodollars"])
            self.assertNotIn("private internal detail", ledger.path.read_text())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

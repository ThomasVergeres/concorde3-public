"""Mechanical qualification only; no API calls or behavior claim."""
import copy
import contextlib
import io
import json
import http.server
import threading
import urllib.request
import urllib.error
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evals import driver
from evals.cases import materialize
from evals.grade import outcome
from evals.lab import PROFILES
from evals.model_transport import MUSE_MODEL, MUSE_PROFILE, api_transport_qualified, transport_ok
from evals.muse_panel import cells, commands
from evals.test_lab import witness
from evals.transport import Proxy


class MuseTransportTests(unittest.TestCase):
    def test_profile_and_defaults_preserved(self):
        self.assertEqual(PROFILES[MUSE_PROFILE], (MUSE_MODEL, "high"))
        self.assertEqual(PROFILES["luna-xhigh"], ("gpt-5.6-luna", "xhigh"))
        self.assertEqual(PROFILES["terra-medium"], ("gpt-5.6-terra", "medium"))
        self.assertEqual(PROFILES['luna6-xhigh'],('gpt-6-luna','xhigh'))
        self.assertEqual(PROFILES['luna6-max'],('gpt-6-luna','max'))
        self.assertEqual(PROFILES['sol6-medium'],('gpt-6-sol','medium'))

    def test_panels_share_cells_not_third_draws(self):
        selection = cells()
        self.assertEqual(len(selection), 72)
        self.assertEqual(sum("historical24" in c["panels"] for c in selection), 24)
        self.assertEqual(sum("active" in c["panels"] for c in selection), 56)
        self.assertEqual(sum(len(c["panels"]) == 2 for c in selection), 8)
        args = SimpleNamespace(output="/unused", fixture="fixture", image="image", api_broker="http://broker/v1/chat/completions",
                               api_broker_token="token", api_adapter="adapter", ledger="ledger")
        dispatch = []
        for group in commands(args):
            self.assertNotIn("--auth", group["command"])
            dispatch.extend((group["case"], variant, int(seed), draw)
                            for variant in ("challenge", "control") for seed in group["worlds"].split(",")
                            for draw in range(group["draws"]))
        self.assertEqual(len(dispatch), len(set(dispatch)))
        self.assertEqual(set(dispatch), {(c["case"], c["variant"], c["world_seed"], c["draw"]) for c in selection})

    def test_archived_panel_cannot_silently_restore_culled_paid_cases(self):
        from evals.muse_panel import main
        args = ["--output", "/unused", "--fixture", "fixture", "--image", "image",
                "--api-broker", "broker", "--api-broker-token", "token",
                "--api-adapter", "adapter", "--execute"]
        with patch("evals.muse_panel.subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(args)
        run.assert_not_called()

    def test_api_preflight_never_logs_in_or_reads_credentials(self):
        with patch.object(driver.subprocess, "check_output", return_value=json.dumps({"protocol":2,"continuation":True})) as command, \
             patch.object(driver.subprocess, "run") as login, patch.object(driver.os, "symlink") as symlink:
            record = driver.model_preflight({"model_transport":"explicit_api", "model":MUSE_MODEL})
        login.assert_not_called(); symlink.assert_not_called()
        self.assertEqual(record["auth_basis"], "explicit_api")
        self.assertEqual(command.call_args.args[0], ["python3", "/run/muse.py", "--concorde-capabilities"])
        with self.assertRaises(RuntimeError): driver.model_preflight({"model_transport":"fallback"})

    def test_transport_provenance_not_subscription_claim(self):
        state = witness()
        activation = state["activations"]["act.witness"]
        activation["config"] = {"harness":"command", "model":MUSE_MODEL, "starts_per_hour":5}
        activation["usage"] = {"basis":"api", "quality":"measured", "input":100, "cached":20, "output":5}
        preflight = {"auth_basis":"explicit_api", "model":MUSE_MODEL, "protocol":2, "continuation":True}
        *_, facts = materialize("BD02")
        result = outcome(facts,state,{}, {"container_stopped":True,"model_preflight":preflight})
        self.assertFalse(result["checks"]["subscription_only"])
        self.assertTrue(result["checks"]["declared_transport"])
        self.assertEqual(result["usage"], {"input":100,"cached":20,"output":5,"quality":"measured"})
        for field, value in (("model","other"),("harness","codex")):
            bad = copy.deepcopy(activation); bad["config"][field] = value
            self.assertFalse(api_transport_qualified([bad], preflight))
        self.assertFalse(api_transport_qualified([activation], {}))
        self.assertFalse(transport_ok({"declared_transport":False, "subscription_only":True}))

    def test_missing_api_usage_remains_unknown(self):
        state = witness(); activation = state["activations"]["act.witness"]
        activation["config"] = {"harness":"command", "model":MUSE_MODEL, "starts_per_hour":5}
        activation["usage"] = {"basis":"command", "quality":"unavailable"}
        *_, facts = materialize("BD02")
        result = outcome(facts,state,{}, {"container_stopped":True})
        self.assertEqual(result["usage"]["quality"], "partial_or_unavailable")
        self.assertFalse(result["checks"]["declared_transport"])

    def test_sidecar_has_fixed_route_and_keeps_bearer_out_of_subject(self):
        seen = []
        class Broker(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                seen.append({"path":self.path, "auth":self.headers.get("Authorization"),
                             "body":self.rfile.read(int(self.headers["Content-Length"]))})
                self.send_response(429); self.send_header("Retry-After", "3")
                self.send_header("X-Concorde-Upstream-Dispatched", "false"); self.end_headers()
                self.wfile.write(b'{"error":"synthetic admission limit"}')
        broker = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Broker)
        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        proxy.api_upstream = f"http://127.0.0.1:{broker.server_port}/v1/chat/completions"
        proxy.api_token = "synthetic-scoped-token"
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (broker, proxy)]
        for t in threads: t.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions", data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as error: opener.open(request)
            self.assertEqual(error.exception.code, 429)
            self.assertEqual(error.exception.headers["Retry-After"], "3")
            self.assertEqual(error.exception.headers["X-Concorde-Upstream-Dispatched"], "false")
            self.assertEqual(seen, [{"path":"/v1/chat/completions", "auth":"Bearer synthetic-scoped-token", "body":b'{}'}])
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(urllib.request.Request(f"http://127.0.0.1:{proxy.server_port}/other", data=b'{}'))
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(len(seen), 1)
        finally:
            for s in (proxy, broker): s.shutdown(); s.server_close()
            for t in threads: t.join()


if __name__ == "__main__": unittest.main()

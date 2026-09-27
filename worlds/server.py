"""Ordinary authenticated HTTP interface. No private scheduler addresses."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit, parse_qs

from .store import Rejected, encoded


def server(world, host="127.0.0.1", port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_): pass

        def send(self, status, data):
            raw = encoded(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def actor(self):
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise Rejected("bearer credential required")
            return world.s.authenticate(auth[7:])

        def do_GET(self):
            try:
                path = urlsplit(self.path)
                if path.path == "/health":
                    return self.send(200, {"transport": "available", "not": "business competence"})
                actor = self.actor()
                if not path.path.startswith("/v1/"):
                    return self.send(404, {"error": "unknown endpoint"})
                params = parse_qs(path.query)
                result = world.view(actor, path.path[4:], int(params.get("offset", [0])[0]))
                self.send(200, result)
            except (Rejected, ValueError, KeyError, TypeError) as error:
                self.send(400, {"error": str(error)[:300]})

        def do_POST(self):
            try:
                if self.path not in ("/v1/actions", "/runtime/reserve", "/runtime/finish"):
                    return self.send(404, {"error": "unknown endpoint"})
                actor = self.actor()
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 100000:
                    raise Rejected("bounded request body required")
                data = json.loads(self.rfile.read(size))
                if self.path.startswith("/runtime/"):
                    from . import runtime
                    if self.path.endswith("reserve"):
                        return self.send(200, runtime.reserve(world, actor, data["activation"], data["phase"], data.get("request_id"), data.get("execution")))
                    return self.send(200, runtime.finish(world, actor, data["id"], data["exit_code"]))
                self.send(200, world.act(actor, self.headers.get("Idempotency-Key", ""), data))
            except (Rejected, ValueError, KeyError, TypeError) as error:
                self.send(400, {"error": str(error)[:300]})

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd

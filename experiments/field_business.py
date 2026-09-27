"""Inherited company-owned service, not an evaluator. Replace or improve freely.

Beacon serves publication.json. Parcel serves catalog.json against incoming
orders. These are minimal already-running businesses, not instructed product plans.
"""
import json
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROLE = sys.argv[1] if len(sys.argv) > 1 else "beacon"
ROOT = Path("/instance")


def read(name):
    return json.loads((ROOT / name).read_text())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        status = 200
        try:
            if ROLE == "beacon":
                if path not in ("/", "/publication"):
                    raise KeyError("not found")
                result = read("publication.json")
            elif path in ("/", "/catalog"):
                result = read("catalog.json")
            elif path.startswith("/delivery/"):
                order_id = path.rsplit("/", 1)[-1]
                orders = json.loads(Path("/exchange/orders.json").read_text())
                order = next(o for o in orders if o["id"] == order_id)
                if not order["paid"]:
                    status, result = 403, {"error": "payment not confirmed"}
                else:
                    product = next(p for p in read("catalog.json")["products"] if p["sku"] == order["sku"])
                    result = {"order": order_id, "sku": product["sku"], "format": "markdown", "content": product["content"]}
            else:
                raise KeyError("not found")
        except (KeyError, StopIteration):
            status, result = 404, {"error": "not found"}
        except Exception:
            status, result = 503, {"error": "service temporarily unavailable"}
        raw = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()

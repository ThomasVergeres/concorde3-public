"""Public GET gateway, independent of the subscription CONNECT transport.

No cookies, credentials, request bodies or arbitrary headers. Resolve every hop,
reject non-public addresses and dial the validated address (no DNS rebinding).
"""
import hashlib
import http.client
import http.server
import ipaddress
import json
import os
import socket
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit, urljoin, parse_qs


def destination(url, resolver=socket.getaddrinfo):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("public http(s) URL without credentials required")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port != (443 if p.scheme == "https" else 80):
        raise ValueError("only ordinary web ports 80/443")
    addresses = {x[4][0] for x in resolver(p.hostname, port, type=socket.SOCK_STREAM)}
    denied = set(os.environ.get("RESEARCH_DENY_IPS", "").split())
    if not addresses or any(not ipaddress.ip_address(a).is_global or a in denied for a in addresses):
        raise ValueError("non-public destination refused")
    return p, sorted(addresses, key=lambda a: (ipaddress.ip_address(a).version, a))[0], port


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, port):
        super().__init__(host, port=port, timeout=8, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), timeout=8)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def fetch(url):
    original = url
    for _ in range(5):
        p, address, port = destination(url)
        conn = PinnedHTTPS(p.hostname, address, port) if p.scheme == "https" else http.client.HTTPConnection(address, port, timeout=8)
        try:
            conn.request("GET", (p.path or "/") + ("?" + p.query if p.query else ""), headers={
                "Host": p.hostname, "User-Agent": "ConcordeResearch/1.0 (public read-only research)", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader("Location")
                if not target:
                    raise ValueError("redirect without destination")
                url = urljoin(url, target)
                continue
            raw = response.read(1000001)
            return {"url": original, "final_url": url, "status": response.status, "content_type": response.getheader("Content-Type", ""),
                    "retrieved_at": time.time(), "truncated": len(raw) > 1000000,
                    "sha256": hashlib.sha256(raw[:1000000]).hexdigest(), "body": raw[:1000000].decode("utf-8", "replace")}
        finally:
            conn.close()
    raise ValueError("redirect limit exceeded")


class Handler(http.server.BaseHTTPRequestHandler):
    cache = {}
    lock = threading.Lock()
    rates = {}

    def log_message(self, *_):
        pass

    def do_GET(self):
        try:
            query = parse_qs(urlsplit(self.path).query)
            if urlsplit(self.path).path != "/fetch":
                raise ValueError("GET /fetch?url=ENCODED_PUBLIC_URL")
            url = query["url"][0]
            if len(url) > 4000:
                raise ValueError("URL too long")
            now = time.time()
            with self.lock:
                slot, n = self.rates.get(self.client_address[0], (int(now // 60), 0))
                n = n + 1 if slot == int(now // 60) else 1
                self.rates[self.client_address[0]] = (int(now // 60), n)
                if n > 60:
                    raise ValueError("60 reads/minute maximum; back off")
                cached = self.cache.get(url)
            result = cached if cached and now - cached["retrieved_at"] < 300 else fetch(url)
            with self.lock:
                if len(self.cache) >= 64:
                    self.cache.clear()
                self.cache[url] = result
            print(json.dumps({"at": now, "host": urlsplit(result["final_url"]).hostname,
                              "status": result["status"], "bytes": len(result["body"].encode()),
                              "cached": result is cached}), flush=True)
            status = 200
        except Exception as error:
            status, result = 400, {"error": type(error).__name__ + ": " + str(error)[:300]}
        raw = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        print(json.dumps(fetch(sys.argv[1])))
    else:
        http.server.ThreadingHTTPServer(("0.0.0.0", 8082), Handler).serve_forever()

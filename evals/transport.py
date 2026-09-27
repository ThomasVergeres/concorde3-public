"""Lab-only CONNECT allowlist. No credentials, evaluator data or general egress."""
import http.server
import select
import socket
import argparse
import ipaddress
import json
import os
import pathlib
import urllib.request
import urllib.error

ALLOWED = {"chatgpt.com:443", "auth.openai.com:443"}
def public_previews():
    """Read private host-to-public-IP pins; never resolve preview DNS here."""
    path = os.environ.get('CONCORDE_PUBLIC_PREVIEWS_FILE')
    if not path:
        return {}
    values = json.loads(pathlib.Path(path).read_text())
    if not isinstance(values, dict):
        raise ValueError('preview pins must be a JSON object')
    for authority, address in values.items():
        if not isinstance(authority, str) or not authority.endswith(':443') or not isinstance(address, str):
            raise ValueError('preview pins require host:443 and public IPv4 address')
        host = authority[:-4]
        if not host or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for c in host):
            raise ValueError('invalid preview hostname')
        if not isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address) or not ipaddress.ip_address(address).is_global:
            raise ValueError('preview pins require a public IPv4 address')
    return values


def preview_mount_args():
    """Pass the operator's private pin file to a Docker transport process."""
    path = os.environ.get('CONCORDE_PUBLIC_PREVIEWS_FILE')
    if not path:
        return []
    public_previews()  # Fail before starting a transport with invalid pins.
    return ['--mount', f'type=bind,source={pathlib.Path(path).resolve()},target=/run/public-previews.json,readonly',
            '--env', 'CONCORDE_PUBLIC_PREVIEWS_FILE=/run/public-previews.json']


def connect_target(authority):
    previews = public_previews()
    if authority in previews:
        return previews[authority], 443
    if authority in ALLOWED:
        host, port = authority.rsplit(":", 1)
        return host, int(port)
    return None

class Proxy(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Destinations/status only, never tokens or tunneled content.
        print(fmt % args, flush=True)

    def do_CONNECT(self):
        if getattr(self.server, "api_upstream", None):
            self.send_error(403, "API-only transport")
            return
        target = connect_target(self.path)
        if target is None:
            self.send_error(403, "destination not authorized")
            return
        try:
            upstream = socket.create_connection(target, timeout=20)
        except OSError:
            self.send_error(502)
            return
        with upstream:
            self.send_response(200, "Connection established")
            self.end_headers()
            sockets = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], 240)
                if not readable:
                    return
                for src in readable:
                    data = src.recv(65536)
                    if not data:
                        return
                    (upstream if src is self.connection else self.connection).sendall(data)

    def do_GET(self):
        self.send_error(403)

    def do_POST(self):
        upstream = getattr(self.server, "api_upstream", None)
        if not upstream or self.path != "/v1/chat/completions":
            self.send_error(403); return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8_388_608:
                self.send_error(413); return
            payload = self.rfile.read(size)
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.server.api_token}
            for key in ("X-Concorde-Deadline", "X-Concorde-Activation", "X-Concorde-Session"):
                if self.headers.get(key): headers[key] = self.headers[key]
            request = urllib.request.Request(upstream, data=payload, headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try: response = opener.open(request, timeout=300)
            except urllib.error.HTTPError as error: response = error
            with response:
                body = response.read(8_388_609)
                if len(body) > 8_388_608: raise ValueError("response exceeds transport bound")
                self.send_response(response.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if response.headers.get("Retry-After"):
                    self.send_header("Retry-After", response.headers["Retry-After"])
                if response.headers.get("X-Concorde-Upstream-Dispatched") in ("true", "false"):
                    self.send_header("X-Concorde-Upstream-Dispatched", response.headers["X-Concorde-Upstream-Dispatched"])
                self.end_headers(); self.wfile.write(body)
        except (OSError, ValueError):
            self.send_error(502, "fixed model route failed")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-upstream")
    parser.add_argument("--api-token-file")
    args = parser.parse_args()
    if bool(args.api_upstream) != bool(args.api_token_file): parser.error("API route and scoped token must be explicit together")
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Proxy)
    server.api_upstream = args.api_upstream
    server.api_token = pathlib.Path(args.api_token_file).read_text().strip() if args.api_token_file else None
    server.serve_forever()

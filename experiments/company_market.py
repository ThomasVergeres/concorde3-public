"""Experiment-local commercial boundary. No Concorde internals or model calls.

SQLite transactions own balances, escrow and idempotency. Product calls are
reserved before dispatch; uncertain calls are never silently replayed.
"""
import hashlib
import http.client
import http.server
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from urllib.parse import urlsplit


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Rejected(Exception):
    pass


class Market:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.config = json.loads((self.root / "config.json").read_text())
        self.db = self.root / "market.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, balance INTEGER NOT NULL CHECK(balance>=0));
                CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS receipts (actor TEXT, key TEXT, fingerprint TEXT, body TEXT, PRIMARY KEY(actor,key));
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, at REAL, actor TEXT, kind TEXT, body TEXT);
            """)
            for name, amount in self.config["balances"].items():
                db.execute("INSERT OR IGNORE INTO accounts VALUES (?,?)", (name, amount))

    def connect(self):
        db = sqlite3.connect(self.db, timeout=20)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def actor(self, token):
        for name, record in self.config["actors"].items():
            if secrets.compare_digest(record["token"], token):
                return name
        raise Rejected("invalid participant credential")

    def rows(self, db, kind):
        return [json.loads(x[0]) for x in db.execute("SELECT body FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def get(self, db, record_id, kind=None):
        row = db.execute("SELECT kind,body FROM records WHERE id=?", (record_id,)).fetchone()
        if not row or (kind and row[0] != kind):
            raise Rejected("record not found")
        return json.loads(row[1])

    def put(self, db, kind, data):
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (data["id"], kind, json.dumps(data)))
        return data

    def event(self, db, actor, kind, data):
        db.execute("INSERT INTO events(at,actor,kind,body) VALUES (?,?,?,?)", (self.clock(), actor, kind, json.dumps(data)))

    def move(self, db, source, destination, amount):
        if type(amount) is not int or amount <= 0:
            raise Rejected("amount must be a positive integer")
        if db.execute("UPDATE accounts SET balance=balance-? WHERE id=? AND balance>=?", (amount, source, amount)).rowcount != 1:
            raise Rejected("insufficient available credits")
        db.execute("INSERT OR IGNORE INTO accounts VALUES (?,0)", (destination,))
        db.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (amount, destination))

    def inject(self, db):
        # Fixed treasury disbursements, not minting. Lazy catch-up is idempotent.
        hours = min(12, max(0, int((min(self.clock(), self.config["cutoff"]) - self.config["started"]) // 3600)))
        for hour in range(1, hours + 1):
            for name, actor in self.config["actors"].items():
                if actor["role"] != "customer":
                    continue
                key = f"injection-{hour}-{name}"
                if db.execute("SELECT 1 FROM records WHERE id=?", (key,)).fetchone():
                    continue
                self.move(db, "treasury", name, 2)
                data = {"id": key, "to": name, "amount": 2, "hour": hour}
                self.put(db, "injection", data)
                self.event(db, "operator", "injection", data)

    def read(self, actor, section):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.inject(db)
            if section == "account":
                result = {"id": actor, "balance": db.execute("SELECT balance FROM accounts WHERE id=?", (actor,)).fetchone()[0],
                          "boosts": [b for b in self.rows(db, "boost") if b["actor"] == actor], "now": self.clock(), "cutoff": self.config["cutoff"]}
            elif section == "directory":
                result = {name: {"role": a["role"], "description": a["description"]} for name, a in self.config["actors"].items()}
            elif section in ("offers", "needs", "reviews"):
                result = self.rows(db, section[:-1])
            elif section == "messages":
                result = [x for x in self.rows(db, "message") if actor in (x["from"], x["to"])]
            elif section == "orders":
                result = [x for x in self.rows(db, "order") if actor in (x["buyer"], x["seller"])]
            elif section == "uses":
                result = [x for x in self.rows(db, "use") if x["actor"] == actor]
            elif section == "incoming_uses":
                # A supplier sees its commercial traffic, not the buyer's private
                # response payload, query string, or other supplier requests.
                result = [{k: x[k] for k in ("id", "at", "actor", "seller", "method", "status", "http_status", "response_sha256") if k in x}
                          for x in self.rows(db, "use") if x["seller"] == actor]
            else:
                raise Rejected("unknown section")
            self.event(db, actor, "read", {"section": section})
            return result

    def act(self, actor, key, data):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise Rejected("Idempotency-Key required (1..128 characters)")
        fingerprint = hashlib.sha256(encoded(data)).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT fingerprint,body FROM receipts WHERE actor=? AND key=?", (actor, key)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise Rejected("idempotency key reused for a different request")
                return json.loads(old[1])
            if self.clock() >= self.config["cutoff"]:
                raise Rejected("experiment frozen")
            self.inject(db)
            result = self.apply(db, actor, data)
            db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (actor, key, fingerprint, json.dumps(result)))
            self.event(db, actor, data["op"], result)
        if data["op"] == "use":
            return self.dispatch(actor, key, result, data)
        return result

    def apply(self, db, actor, data):
        op = data.get("op")
        identity = secrets.token_hex(10)
        at = self.clock()
        base = {"id": identity, "at": at}
        if op in ("offer", "need"):
            text = data.get("text", "")
            if not isinstance(text, str) or not 1 <= len(text) <= 12000:
                raise Rejected("bounded nonempty text required")
            return self.put(db, op, dict(base, owner=actor, text=text, supersedes=data.get("supersedes")))
        if op == "message":
            if data.get("to") not in self.config["actors"] or data["to"] == actor:
                raise Rejected("choose another participant")
            if not isinstance(data.get("text"), str) or not 1 <= len(data["text"]) <= 12000:
                raise Rejected("bounded nonempty text required")
            return self.put(db, op, dict(base, **{"from": actor, "to": data["to"], "text": data["text"]}))
        if op == "order":
            seller, terms, amount = data.get("seller"), data.get("terms"), data.get("amount")
            if seller not in self.config["actors"] or seller == actor or not isinstance(terms, str) or not 1 <= len(terms) <= 12000:
                raise Rejected("another seller and explicit agreed/proposed terms required")
            self.move(db, actor, "escrow", amount)
            return self.put(db, op, dict(base, buyer=actor, seller=seller, amount=amount, terms=terms, status="ordered"))
        if op in ("deliver", "accept", "reject", "cancel"):
            order = self.get(db, data.get("order"), "order")
            if order["status"] not in ("ordered", "delivered"):
                raise Rejected("order already terminal")
            reason = data.get("reason", "")
            if not isinstance(reason, str) or not 1 <= len(reason) <= 12000:
                raise Rejected("reason/evidence required")
            if op == "deliver":
                if actor != order["seller"]:
                    raise Rejected("only seller may deliver")
                order.update(status="delivered", delivery=reason)
            else:
                if actor != order["buyer"]:
                    raise Rejected("only buyer may accept/reject/cancel")
                if op == "accept":
                    if order["status"] != "delivered":
                        raise Rejected("seller has not delivered")
                    use = self.get(db, data.get("use"), "use")
                    if use["actor"] != actor or use["seller"] != order["seller"] or use["status"] != "returned" or not 200 <= use["http_status"] < 300 or use["at"] < order["at"]:
                        raise Rejected("requires your successful post-order supplier-use receipt")
                    alternative = data.get("alternative")
                    if not isinstance(alternative, str) or not 1 <= len(alternative) <= 4000:
                        raise Rejected("state the build/buy/do-nothing alternative you considered")
                    self.move(db, "escrow", order["seller"], order["amount"])
                    order.update(status="accepted", use=use["id"], alternative=alternative)
                else:
                    self.move(db, "escrow", actor, order["amount"])
                    order["status"] = "rejected" if op == "reject" else "canceled"
                order.update(decision=reason, decided_at=at)
                if op != "cancel":
                    self.put(db, "review", dict(base, buyer=actor, seller=order["seller"], order=order["id"], decision=op, reason=reason))
            return self.put(db, "order", order)
        if op == "boost":
            if self.config["actors"][actor]["role"] != "venture" and not self.config.get("allow_customer_boost"):
                raise Rejected("customer attention is fixed for experimental comparability")
            if self.config["cutoff"] - at < 3600:
                raise Rejected("less than a full lease hour remains")
            if any(x["actor"] == actor and x["until"] > at for x in self.rows(db, "boost")):
                raise Rejected("already have an active capacity lease")
            self.move(db, actor, "capacity", 4)
            cap = self.config["actors"][actor].get("boosted_starts", 8)
            return self.put(db, "boost", dict(base, actor=actor, until=at + 3600, amount=4, starts_per_hour=cap))
        if op == "use":
            seller = data.get("seller")
            path, method = data.get("path", "/"), data.get("method", "GET")
            parsed = urlsplit(path)
            if seller not in self.config["actors"] or seller == actor or method not in ("GET", "POST") or not path.startswith("/") or parsed.netloc or parsed.scheme or any(ord(c) < 32 for c in path):
                raise Rejected("use another participant's port-8000 service with GET/POST and a relative /path")
            if not isinstance(data.get("body", ""), str) or len(data.get("body", "").encode()) > 16000:
                raise Rejected("request body must be text <=16000 bytes")
            return self.put(db, "use", dict(base, actor=actor, seller=seller, method=method, path=path, status="reserved", request_sha256=hashlib.sha256(encoded(data)).hexdigest()))
        raise Rejected("unknown operation")

    def dispatch(self, actor, key, receipt, data):
        # Host/port are operator-pinned, not arbitrary URLs (no host SSRF).
        conn = http.client.HTTPConnection(self.config["actors"][receipt["seller"]]["product_host"], 8000, timeout=8)
        try:
            conn.request(receipt["method"], receipt["path"], body=data.get("body", ""), headers={"Content-Type": "application/json", "X-Market-Customer": actor, "Idempotency-Key": receipt["id"]})
            response = conn.getresponse()
            body = response.read(65537)
            if len(body) > 65536:
                raise Rejected("response larger than 65536 bytes; use product pagination")
            receipt.update(status="returned", http_status=response.status, content_type=response.getheader("Content-Type", ""), body=body.decode("utf-8", "replace"), response_sha256=hashlib.sha256(body).hexdigest())
        except Exception as error:
            receipt.update(status="uncertain", error=type(error).__name__ + ": " + str(error)[:200])
        finally:
            conn.close()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.put(db, "use", receipt)
            db.execute("UPDATE receipts SET body=? WHERE actor=? AND key=?", (json.dumps(receipt), actor, key))
            self.event(db, actor, "use_result", receipt)
        return receipt


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, market):
        super().__init__(address, Handler)
        self.market = market
        self.lock = threading.Lock()
        self.rates = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Credentials/request bodies must not enter access logs.

    def reply(self, status, value):
        body = encoded(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self, write=False):
        self.connection.settimeout(12)
        try:
            actor = self.server.market.actor(self.headers.get("Authorization", "").removeprefix("Bearer "))
            with self.server.lock:
                slot = int(time.time() // 60)
                previous, count = self.server.rates.get(actor, (slot, 0))
                count = count + 1 if previous == slot else 1
                self.server.rates[actor] = (slot, count)
                if count > 120:
                    self.reply(429, {"error": "120 requests/minute per participant; back off"})
                    return
            if write:
                if self.path != "/v1/actions":
                    raise Rejected("use POST /v1/actions")
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 32768:
                    raise Rejected("body bound is 32768 bytes")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise Rejected("object required")
                result = self.server.market.act(actor, self.headers.get("Idempotency-Key"), data)
            else:
                if not self.path.startswith("/v1/"):
                    raise Rejected("use GET /v1/SECTION")
                query = urlsplit(self.path)
                result = self.server.market.read(actor, query.path.removeprefix("/v1/"))
                if isinstance(result, list):
                    from urllib.parse import parse_qs
                    offset = max(0, int(parse_qs(query.query).get("offset", ["0"])[0]))
                    selected = []
                    for item in result[offset:offset + 25]:
                        if selected and len(encoded(selected + [item])) > 90000:
                            break
                        selected.append(item)
                    result = {"items": selected, "total": len(result), "next_offset": offset + len(selected) if offset + len(selected) < len(result) else None}
            self.reply(200, result)
        except (Rejected, ValueError, TypeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception:
            self.reply(503, {"error": "market unavailable; reconcile with same idempotency key"})

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request(True)


if __name__ == "__main__":
    Server(("0.0.0.0", 8081), Market(sys.argv[1])).serve_forever()

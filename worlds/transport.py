"""Bounded real product calls, reserved before dispatch; no host code execution."""
import http.client
import json
from urllib.parse import urlsplit
from .store import require, digest, encoded


def reserve(world, db, actor, data):
    seller = data["seller"]
    world.s.actor(db, seller)
    require(seller != actor, "use another participant's public product")
    endpoints = world.s.meta(db, "endpoints") or {}
    require(seller in endpoints, "supplier has no reachable service endpoint")
    path, method = data.get("path", "/"), data.get("method", "GET")
    parsed = urlsplit(path)
    require(isinstance(path, str) and path.startswith("/") and not parsed.netloc and not parsed.scheme and not any(ord(c)<32 for c in path), "relative product path required")
    require(method in ("GET", "POST"), "GET/POST only")
    body = data.get("body", "")
    require(isinstance(body, str) and len(body.encode()) <= 16000, "bounded body required")
    return world.s.put(db, "use", actor, {"seller": seller, "path": path, "method": method, "request_body": body,
        "endpoint": endpoints[seller], "status": "reserved", "request_hash": digest(data), "at": world.s.clock()})


def dispatch(world, actor, identity):
    s = world.s
    with s.transaction() as db:
        r = s.get(db, identity, actor, "use")
        if r["status"] != "reserved":
            return r
        s.alive(db)
        r = s.revise(db, r, status="dispatching")
        cutoff = s.meta(db, "config")["cutoff"]
    conn = None
    try:
        conn = http.client.HTTPConnection(r["endpoint"]["host"], r["endpoint"].get("port", 8000), timeout=max(.1, min(8, cutoff-s.clock())))
        conn.request(r["method"], r["path"], body=r["request_body"], headers={"Content-Type": "application/json", "Idempotency-Key": identity})
        response = conn.getresponse()
        raw = response.read(65537)
        require(len(raw) <= 65536, "product response exceeded limit")
        try: content = json.loads(raw)
        except (ValueError, UnicodeDecodeError): content = raw.decode(errors="replace")
        with s.transaction() as db:
            current = s.get(db, identity)
            used = sum(len(encoded(a["content"]).encode()) for a in s.rows(db, "artifact") if a["owner"] == actor)
            quota = s.get(db, "quota:"+actor)["bytes"]
            if used+len(encoded(content).encode()) > quota or len(encoded(content).encode()) > 65536:
                result = s.revise(db, current, status="returned_unstored", http_status=response.status, response_hash=digest(content), error="Receiving artifact storage quota exceeded; response was received, not replayed")
                s.event(db, "_transport", "product_returned_unstored", {"use": identity, "http_status": response.status, "response_hash": digest(content)})
                return result
            artifact = s.put(db, "artifact", actor, {"title": "Received product output", "content": content, "producer": r["seller"], "use": identity, "at": s.clock()})
            result = s.revise(db, current, status="returned", http_status=response.status, artifact=artifact["id"], response_hash=digest(content))
            s.event(db, "_transport", "product_returned", {"use": identity, "http_status": response.status, "artifact": artifact["id"]})
            return result
    except Exception as error:
        with s.transaction() as db:
            current = s.get(db, identity)
            result = s.revise(db, current, status="uncertain", error=str(error)[:300])
            s.event(db, "_transport", "product_uncertain", {"use": identity, "error": str(error)[:300]})
            return result
    finally:
        if conn: conn.close()

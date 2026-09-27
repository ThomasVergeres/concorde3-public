"""Small transactional world store: scoped records, immutable events and receipts."""
import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time


class Rejected(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise Rejected(message)


def bounded(value, limit=12000):
    require(isinstance(value, str) and 0 < len(value.encode()) <= limit, "nonempty bounded text required")
    return value


class Store:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.path = self.root / "world.sqlite"

    @contextlib.contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, config, actors):
        require(not self.root.exists(), "refusing to overwrite a world")
        require(config["cutoff"] > config["started"], "finite future execution window required")
        self.root.mkdir(parents=True, mode=0o700)
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE actors(id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
                CREATE TABLE records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner TEXT NOT NULL,
                    audience TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE TABLE events(seq INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
                    kind TEXT NOT NULL, cause TEXT, body TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE receipts(actor TEXT, key TEXT, fingerprint TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(actor,key));
                CREATE TABLE balances(account TEXT PRIMARY KEY, amount INTEGER NOT NULL CHECK(amount>=0));
                CREATE TABLE postings(seq INTEGER PRIMARY KEY, event INTEGER NOT NULL, source TEXT NOT NULL,
                    destination TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), category TEXT NOT NULL,
                    contract TEXT, FOREIGN KEY(event) REFERENCES events(seq));
                CREATE TABLE calls(id TEXT PRIMARY KEY, actor TEXT NOT NULL, category TEXT NOT NULL,
                    at REAL NOT NULL, deadline REAL NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX record_kind ON records(kind);
                CREATE INDEX call_time ON calls(at);
            """)
        with self.transaction() as db:
            self.meta(db, "config", config)
            self.meta(db, "frozen", False)
            for actor, body in actors.items():
                require(not actor.startswith("_"), "reserved actor prefix")
                db.execute("INSERT INTO actors VALUES (?,?,?)", (actor, secrets.token_urlsafe(32), encoded(body)))
            self.event(db, "_operator", "created", {"config": config, "actors": actors})

    def meta(self, db, key, value=None):
        if value is not None:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, encoded(value)))
            return value
        row = db.execute("SELECT body FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def alive(self, db):
        require(not self.meta(db, "frozen") and self.clock() < self.meta(db, "config")["cutoff"], "world frozen")

    def event(self, db, actor, kind, body, cause=None):
        cause = str(cause) if cause is not None else None
        previous = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prior = previous[0] if previous else ""
        at = self.clock()
        hashed = digest([at, actor, kind, cause, body, prior])
        cur = db.execute("INSERT INTO events(at,actor,kind,cause,body,previous,hash) VALUES (?,?,?,?,?,?,?)",
                         (at, actor, kind, str(cause) if cause is not None else None, encoded(body), prior, hashed))
        return cur.lastrowid

    def put(self, db, kind, owner, body, audience=(), record_id=None, revision=None):
        record_id = record_id or secrets.token_hex(12)
        old = db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        require(old is None or (old["owner"] == owner and old["kind"] == kind), "record ownership/type mismatch")
        require(revision is None or (old["revision"] if old else 0) == revision, "revision conflict")
        rev = old["revision"] + 1 if old else 1
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)",
                   (record_id, kind, owner, encoded(list(audience)), rev, encoded(body)))
        return {"id": record_id, "kind": kind, "owner": owner, "revision": rev, **body}

    def get(self, db, record_id, actor=None, kind=None):
        row = db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        require(row is not None, "record unavailable")
        audience = json.loads(row["audience"])
        require(actor is None or actor == row["owner"] or actor in audience or "*" in audience, "record unavailable")
        require(kind is None or kind == row["kind"], "wrong record type")
        return {"id": row["id"], "kind": row["kind"], "owner": row["owner"], "revision": row["revision"], **json.loads(row["body"])}

    def rows(self, db, kind, actor=None):
        result = []
        for row in db.execute("SELECT * FROM records WHERE kind=? ORDER BY rowid", (kind,)):
            audience = json.loads(row["audience"])
            if actor is None or actor == row["owner"] or actor in audience or "*" in audience:
                result.append(self.get(db, row["id"]))
        return result

    def revise(self, db, record, **changes):
        row = db.execute("SELECT audience FROM records WHERE id=?", (record["id"],)).fetchone()
        body = {k: v for k, v in record.items() if k not in ("id", "kind", "owner", "revision")}
        body.update(changes)
        return self.put(db, record["kind"], record["owner"], body, json.loads(row[0]), record["id"], record["revision"])

    def actor(self, db, actor):
        row = db.execute("SELECT body FROM actors WHERE id=?", (actor,)).fetchone()
        require(row is not None, "unknown actor")
        return json.loads(row[0])

    def authenticate(self, token):
        with self.transaction() as db:
            row = db.execute("SELECT id FROM actors WHERE token=?", (token,)).fetchone()
            require(row is not None, "invalid credential")
            return row[0]

    def action(self, actor, key, data, apply):
        bounded(key, 128)
        require(len(encoded(data).encode()) <= 100000, "action exceeds bound")
        fingerprint = digest(data)
        with self.transaction() as db:
            self.actor(db, actor)
            previous = db.execute("SELECT * FROM receipts WHERE actor=? AND key=?", (actor, key)).fetchone()
            if previous:
                require(previous["fingerprint"] == fingerprint, "idempotency key/body conflict")
                return json.loads(previous["body"])
            self.alive(db)
            result = apply(db, actor, data)
            event = self.event(db, actor, data["op"], {"request": data, "result": result})
            result = {"event": event, "result": result}
            db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (actor, key, fingerprint, encoded(result)))
            return result

    def verify(self):
        with self.transaction() as db:
            prior = ""
            for e in db.execute("SELECT * FROM events ORDER BY seq"):
                body = json.loads(e["body"])
                require(e["previous"] == prior and e["hash"] == digest([e["at"], e["actor"], e["kind"], e["cause"], body, prior]), "event integrity failure")
                prior = e["hash"]
            return {"events": db.execute("SELECT count(*) FROM events").fetchone()[0], "head": prior}

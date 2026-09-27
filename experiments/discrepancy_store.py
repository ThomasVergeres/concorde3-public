"""Durable private queue for discrepancy-lab evidence and bounded interventions.

The store indexes evidence; immutable forensic blobs and model-call directories
remain the source records.  A signal nominates a suspected case.  It is never an
automatic failure label or permission to change Concorde.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time


def case_scope_matches(scopes, subject):
    if not isinstance(subject, str): return False
    world, separator, actor = subject.partition("/")
    return bool(separator and actor and any(scope["world"] == world and
        (scope["subject"] is None or scope["subject"] == actor) for scope in scopes))


SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rounds(
  number INTEGER PRIMARY KEY, scheduled REAL NOT NULL, started REAL, finished REAL,
  status TEXT NOT NULL, capture_ref TEXT, review_ref TEXT, lateness REAL,
  CHECK(status IN ('scheduled','capturing','reviewing','captured','reviewed','gap','failed','terminal'))
);
CREATE TABLE IF NOT EXISTS cases(
  id TEXT PRIMARY KEY, episode_key TEXT UNIQUE NOT NULL, first_round INTEGER NOT NULL,
  last_round INTEGER NOT NULL, status TEXT NOT NULL, category TEXT NOT NULL,
  sightings INTEGER NOT NULL DEFAULT 1, selected INTEGER NOT NULL DEFAULT 0,
  CHECK(status IN ('suspected','healthy_control','quiet_sample','curating','qualified',
                   'quarantined','analogy_not_reproduced','probe_inconclusive',
                   'candidate','candidate_rejected','candidate_inconclusive','closed'))
);
CREATE TABLE IF NOT EXISTS signals(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), round INTEGER NOT NULL,
  world TEXT NOT NULL, subject TEXT, category TEXT NOT NULL, polarity TEXT NOT NULL,
  severity TEXT NOT NULL, evidence_ref TEXT NOT NULL, observed TEXT NOT NULL,
  exposure TEXT NOT NULL, censored INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
  UNIQUE(round, world, category, evidence_ref)
);
CREATE TABLE IF NOT EXISTS reviews(
  id TEXT PRIMARY KEY, round INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  model TEXT NOT NULL, effort TEXT NOT NULL, packet_sha256 TEXT, prompt_sha256 TEXT,
  response_ref TEXT, usage TEXT, started REAL NOT NULL, finished REAL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS judgments(
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id), case_id TEXT,
  subject TEXT NOT NULL, category TEXT NOT NULL, severity TEXT NOT NULL,
  confidence TEXT NOT NULL, likely_layer TEXT NOT NULL, observed TEXT NOT NULL,
  available_context TEXT NOT NULL, hindsight TEXT NOT NULL, preferable TEXT NOT NULL,
  ideas TEXT NOT NULL, evidence TEXT NOT NULL, disposition TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), kind TEXT NOT NULL,
  status TEXT NOT NULL, specification TEXT NOT NULL, criteria_sha256 TEXT,
  branch TEXT, worktree TEXT, result_ref TEXT, created REAL NOT NULL, updated REAL NOT NULL,
  CHECK(kind IN ('curation','baseline_probe','candidate_build','candidate_probe','independent_review')),
  CHECK(status IN ('queued','running','passed','failed','rejected','censored'))
);
CREATE TABLE IF NOT EXISTS calls(
  id TEXT PRIMARY KEY, purpose TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
  status TEXT NOT NULL, started REAL NOT NULL, finished REAL, usage TEXT,
  evidence_ref TEXT, error TEXT,
  CHECK(status IN ('reserved','running','completed','failed','censored'))
);
"""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def identity(prefix, *parts):
    raw = encoded(parts).encode()
    return prefix + "-" + hashlib.sha256(raw).hexdigest()[:20]


class DiscrepancyStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def event(self, db, kind, body):
        db.execute("INSERT INTO events(at,kind,body) VALUES (?,?,?)",
                   (self.clock(), kind, encoded(body)))

    def schedule_rounds(self, started, cutoff, interval=600, *, windows=18):
        if interval not in (600, 1200) or type(windows) is not int or not 2 <= windows <= 18 or cutoff - started != windows * interval:
            raise ValueError("campaign requires its declared exact ten- or twenty-minute review windows")
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM rounds").fetchone()[0]:
                raise ValueError("round schedule is immutable")
            # Round zero is capture-only; the last review follows dispatch freeze.
            db.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?)",
                       (0, started, None, None, "scheduled", None, None, None))
            for number in range(1, windows+1):
                db.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?)",
                           (number, started + number * interval, None, None,
                            "scheduled", None, None, None))
            self.event(db, "round_schedule_frozen", {"started": started,
                "cutoff": cutoff, "interval": interval, "intelligent_reviews": windows,
                "round_zero": "initial capture only", "terminal_round": windows})

    def start_round(self, number, status="capturing"):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM rounds WHERE number=?", (number,)).fetchone()
            if row is None or row["status"] != "scheduled":
                raise ValueError("round is absent or already attempted")
            db.execute("UPDATE rounds SET started=?,status=?,lateness=? WHERE number=?",
                       (now, status, max(0, now-row["scheduled"]), number))
            self.event(db, "round_started", {"number": number, "scheduled": row["scheduled"], "actual": now})
        return now

    def finish_round(self, number, status, capture_ref=None, review_ref=None, error=None):
        if status not in ("captured", "reviewed", "gap", "failed", "terminal"):
            raise ValueError("invalid terminal round status")
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM rounds WHERE number=?", (number,)).fetchone()
            if row is None or row["status"] not in ("capturing", "reviewing"):
                raise ValueError("round was not started")
            db.execute("UPDATE rounds SET finished=?,status=?,capture_ref=?,review_ref=? WHERE number=?",
                       (now, status, capture_ref, review_ref, number))
            self.event(db, "round_finished", {"number": number, "status": status,
                "capture_ref": capture_ref, "review_ref": review_ref, "error": error})

    def nominate(self, *, round_number, world, subject, category, polarity,
                 severity, evidence_ref, observed, exposure, episode_key, censored=False):
        case_id = identity("case", episode_key)
        signal_id = identity("signal", round_number, world, category, evidence_ref)
        status = "healthy_control" if polarity == "positive" else "quiet_sample" if category == "quiet_sample" else "quarantined" if polarity == "neutral" else "suspected"
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing_signal = db.execute("SELECT case_id FROM signals WHERE id=?", (signal_id,)).fetchone()
            if existing_signal is not None:
                # Reprocessing with a changed detector/grouping is not authority
                # to rewrite an original signal or create an ungrounded case.
                return existing_signal["case_id"]
            previous = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if previous is None:
                db.execute("INSERT INTO cases(id,episode_key,first_round,last_round,status,category) VALUES (?,?,?,?,?,?)",
                           (case_id, episode_key, round_number, round_number, status, category))
            inserted = db.execute("INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (signal_id, case_id, round_number, world, subject, category, polarity,
                 severity, evidence_ref, observed, encoded(exposure), int(censored), now)).rowcount
            if inserted and previous is not None:
                db.execute("UPDATE cases SET first_round=MIN(first_round,?),last_round=MAX(last_round,?),sightings=sightings+1 WHERE id=?",
                           (round_number, round_number, case_id))
            if inserted:
                self.event(db, "signal_nominated", {"signal": signal_id, "case": case_id,
                    "category": category, "polarity": polarity, "censored": censored})
        return case_id

    def begin_review(self, number, kind, model="gpt-5.6-sol", effort="high"):
        review_id = identity("review", number, kind, self.clock())
        with self.connect() as db:
            db.execute("INSERT INTO reviews(id,round,kind,status,model,effort,started) VALUES (?,?,?,?,?,?,?)",
                       (review_id, number, kind, "running", model, effort, self.clock()))
            self.event(db, "review_started", {"review": review_id, "round": number, "kind": kind,
                "model": model, "effort": effort})
        return review_id

    def begin_call(self, purpose, model="gpt-5.6-sol", effort="xhigh", *, maximum=None):
        if maximum is not None and (type(maximum) is not int or maximum < 0):
            raise ValueError("nonnegative integer call limit required")
        call_id = identity("call", purpose, self.clock())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if maximum is not None and db.execute("SELECT count(*) FROM calls").fetchone()[0] >= maximum:
                raise RuntimeError("finite campaign meta-call limit reached")
            db.execute("INSERT INTO calls(id,purpose,model,effort,status,started) VALUES (?,?,?,?,?,?)",
                       (call_id, purpose, model, effort, "reserved", self.clock()))
            self.event(db, "call_reserved", {"call": call_id, "purpose": purpose,
                "model": model, "effort": effort})
        return call_id

    def call_running(self, call_id, evidence_ref):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row["status"] != "reserved":
                raise ValueError("call is not reserved")
            db.execute("UPDATE calls SET status='running',evidence_ref=? WHERE id=?",
                       (evidence_ref, call_id))
            self.event(db, "call_running", {"call": call_id, "evidence_ref": evidence_ref})

    def finish_call(self, call_id, status, *, usage=None, evidence_ref=None, error=None):
        if status not in ("completed", "failed", "censored"):
            raise ValueError("invalid call status")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row["status"] not in ("reserved", "running"):
                raise ValueError("call is not active")
            db.execute("UPDATE calls SET status=?,finished=?,usage=?,evidence_ref=COALESCE(?,evidence_ref),error=? WHERE id=?",
                       (status, self.clock(), encoded(usage) if usage is not None else None,
                        evidence_ref, error, call_id))
            self.event(db, "call_finished", {"call": call_id, "status": status,
                "evidence_ref": evidence_ref, "error": error})

    def finish_review(self, review_id, *, status, packet_sha256=None,
                      prompt_sha256=None, response_ref=None, usage=None, error=None):
        if status not in ("completed", "completed_partial", "failed", "censored"):
            raise ValueError("invalid review status")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM reviews WHERE id=?", (review_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise ValueError("review is not running")
            db.execute("UPDATE reviews SET status=?,packet_sha256=?,prompt_sha256=?,response_ref=?,usage=?,finished=?,error=? WHERE id=?",
                       (status, packet_sha256, prompt_sha256, response_ref,
                        encoded(usage) if usage is not None else None, self.clock(), error, review_id))
            self.event(db, "review_finished", {"review": review_id, "status": status,
                "response_ref": response_ref, "error": error})

    def add_judgment(self, review_id, judgment):
        refs = judgment["evidence"]
        identity_value = identity("judgment", review_id, judgment.get("case_id"),
                                  judgment["subject"], refs, judgment["observed"])
        with self.connect() as db:
            if judgment.get("case_id"):
                scopes = db.execute("SELECT world,subject FROM signals WHERE case_id=?",
                    (judgment["case_id"],)).fetchall()
                if not case_scope_matches(scopes, judgment["subject"]):
                    raise ValueError("judgment subject conflicts with canonical case scope")
            db.execute("INSERT INTO judgments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity_value, review_id, judgment.get("case_id"), judgment["subject"],
                 judgment["category"], judgment["severity"], judgment["confidence"],
                 judgment["likely_layer"], judgment["observed"], judgment["available_context"],
                 judgment["hindsight"], judgment["preferable"], encoded(judgment["ideas"]),
                 encoded(refs), judgment["disposition"]))
            self.event(db, "judgment_recorded", {"judgment": identity_value,
                "review": review_id, "case": judgment.get("case_id"),
                "disposition": judgment["disposition"]})
            status = {"healthy_control": "healthy_control", "quarantine": "quarantined", "censored": "quarantined"}.get(judgment["disposition"])
            if status and judgment.get("case_id"):
                # Preserve selected/advanced repair evidence. Advisory review
                # must not leave an unselected healthy/unknown case as a failure.
                changed = db.execute("UPDATE cases SET status=? WHERE id=? AND selected=0 AND status IN ('suspected','healthy_control','quiet_sample','quarantined')", (status, judgment["case_id"])).rowcount
                if changed:
                    self.event(db, "case_disposition_reviewed", {"case": judgment["case_id"], "status": status, "judgment": identity_value, "review": review_id})
        return identity_value

    def create_job(self, case_id, kind, specification, status="queued"):
        job_id = identity("job", case_id, kind, self.clock())
        now = self.clock()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise ValueError("unknown case")
            db.execute("INSERT INTO jobs(id,case_id,kind,status,specification,created,updated) VALUES (?,?,?,?,?,?,?)",
                       (job_id, case_id, kind, status, encoded(specification), now, now))
            self.event(db, "job_created", {"job": job_id, "case": case_id,
                "kind": kind, "status": status})
        return job_id

    def set_case(self, case_id, status, selected=None):
        allowed = {"suspected", "healthy_control", "quiet_sample", "curating", "qualified",
                   "quarantined", "analogy_not_reproduced", "probe_inconclusive",
                   "candidate", "candidate_rejected", "candidate_inconclusive", "closed"}
        if status not in allowed:
            raise ValueError("invalid case status")
        with self.connect() as db:
            if db.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise ValueError("unknown case")
            db.execute("UPDATE cases SET status=?,selected=COALESCE(?,selected) WHERE id=?",
                       (status, int(selected) if selected is not None else None, case_id))
            self.event(db, "case_updated", {"case": case_id, "status": status,
                "selected": selected})

    def update_job(self, job_id, status, *, result_ref=None, criteria_sha256=None,
                   branch=None, worktree=None):
        if status not in ("running", "passed", "failed", "rejected", "censored"):
            raise ValueError("invalid job status")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in ("queued", "running"):
                raise ValueError("job is not active")
            db.execute("UPDATE jobs SET status=?,result_ref=COALESCE(?,result_ref),criteria_sha256=COALESCE(?,criteria_sha256),branch=COALESCE(?,branch),worktree=COALESCE(?,worktree),updated=? WHERE id=?",
                       (status, result_ref, criteria_sha256, branch, worktree, self.clock(), job_id))
            self.event(db, "job_updated", {"job": job_id, "status": status,
                "result_ref": result_ref})

    def rows(self, table, where="1", args=()):
        if table not in {"rounds", "cases", "signals", "reviews", "judgments", "jobs", "calls", "events"}:
            raise ValueError("unknown table")
        with self.connect() as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE {where}", args)]

    def censor_open(self, reason, *, preserve_terminal=False, terminal_round=18):
        if type(terminal_round) is not int or not 2 <= terminal_round <= 18:
            raise ValueError("bounded terminal review round required")
        with self.connect() as db:
            now = self.clock()
            db.execute("UPDATE jobs SET status='censored',updated=? WHERE status IN ('queued','running')", (now,))
            call_filter = f" AND purpose!='review:{terminal_round}'" if preserve_terminal else ""
            review_filter = f" AND NOT (round={terminal_round} AND kind='terminal')" if preserve_terminal else ""
            db.execute("UPDATE calls SET status='censored',finished=?,error=? WHERE status IN ('reserved','running')" + call_filter,
                       (now, reason))
            db.execute("UPDATE reviews SET status='censored',finished=?,error=? WHERE status='running'" + review_filter,
                       (now, reason))
            self.event(db, "open_work_censored", {"reason": reason, "preserve_terminal": preserve_terminal})

"""Deterministic freeze-versus-completion races on real SQLite connections."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from experiments.discrepancy_store import DiscrepancyStore


class TransitionRaceTests(unittest.TestCase):
    def test_freeze_committed_first_remains_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscrepancyStore(Path(tmp)/"state.sqlite")
            call = store.begin_call("fixture")
            review = store.begin_review(1, "interval")
            case = store.nominate(round_number=1, world="w", subject="a", category="x",
                polarity="suspected", severity="low", evidence_ref="x", observed="x", exposure={}, episode_key="x")
            job = store.create_job(case, "curation", {})
            store.censor_open("fixed cutoff")
            for finish in (lambda: store.finish_call(call, "completed"),
                    lambda: store.finish_review(review, status="completed"),
                    lambda: store.update_job(job, "passed"), lambda: store.call_running(call, "late")):
                with self.assertRaises(ValueError): finish()
            for table in ("calls", "reviews", "jobs"):
                self.assertEqual(store.rows(table)[0]["status"], "censored")

    def test_completion_never_overwrites_an_already_committed_freeze(self):
        for table in ("calls", "reviews", "jobs"):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/"state.sqlite"
                worker, freezer = DiscrepancyStore(path), DiscrepancyStore(path)
                if table == "calls":
                    identity = worker.begin_call("fixture")
                    finish = lambda: worker.finish_call(identity, "completed")
                    finished_event = "call_finished"
                elif table == "reviews":
                    identity = worker.begin_review(1, "interval")
                    finish = lambda: worker.finish_review(identity, status="completed")
                    finished_event = "review_finished"
                else:
                    case = worker.nominate(round_number=1, world="w", subject="a", category="x",
                        polarity="suspected", severity="low", evidence_ref="x", observed="x", exposure={}, episode_key="x")
                    identity = worker.create_job(case, "curation", {})
                    finish = lambda: worker.update_job(identity, "passed")
                    finished_event = "job_updated"
                read, release = threading.Event(), threading.Event()
                original_connect = worker.connect

                class CursorGate:
                    def __init__(self, cursor): self.cursor = cursor
                    def fetchone(self):
                        row = self.cursor.fetchone()
                        read.set()
                        if not release.wait(3): raise RuntimeError("test gate timeout")
                        return row

                class ConnectionGate:
                    def __init__(self): self.db = original_connect()
                    def __enter__(self): self.db.__enter__(); return self
                    def __exit__(self, *args): return self.db.__exit__(*args)
                    def execute(self, query, args=()):
                        cursor = self.db.execute(query, args)
                        return CursorGate(cursor) if query.startswith("SELECT status FROM "+table) else cursor

                with patch.object(worker, "connect", side_effect=ConnectionGate), ThreadPoolExecutor(max_workers=2) as pool:
                    completing = pool.submit(finish)
                    self.assertTrue(read.wait(3))
                    freezing = pool.submit(freezer.censor_open, "fixed cutoff")
                    try: freezing.result(timeout=.1)
                    except TimeoutError: pass  # Correct writer lock holds freeze until completion commits.
                    finally: release.set()
                    completing.result(timeout=3); freezing.result(timeout=3)
                rows = freezer.rows("events")
                freeze_seq = next(r["seq"] for r in rows if r["kind"] == "open_work_censored")
                finish_seq = next(r["seq"] for r in rows if r["kind"] == finished_event)
                self.assertLess(finish_seq, freeze_seq,
                    "a completion observed pre-freeze active state, then overwrote a committed censor")
                self.assertIn(freezer.rows(table, "id=?", (identity,))[0]["status"], ("completed", "passed"))

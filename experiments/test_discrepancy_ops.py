import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.discrepancy_health import sample
from experiments.discrepancy_campaign import source_hashes
from experiments.discrepancy_shadow import run
from experiments.discrepancy_store import DiscrepancyStore
from experiments.discrepancy_transport import stop_active
from worlds import budget
from worlds.engine import World


class OperationsTests(unittest.TestCase):
    def test_health_detects_a_review_window_that_never_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"cohort.json").write_text(json.dumps({"status": "running", "worlds": {}, "cutoff": 11300}))
            store = DiscrepancyStore(root/"discrepancies.sqlite")
            store.schedule_rounds(500, 11300)
            with patch("experiments.discrepancy_health.time.time", return_value=1699):
                self.assertFalse(sample(root)["warnings"])
            with patch("experiments.discrepancy_health.time.time", return_value=1701):
                result = sample(root)
            self.assertTrue(any(w.get("kind") == "review_window_missed" and w.get("rounds") == [1]
                                for w in result["warnings"]))
            self.assertEqual(store.rows("reviews"), [])
            self.assertTrue(all(r["status"] == "scheduled" for r in store.rows("rounds")))

    def test_health_does_not_relabel_an_observed_complete_or_partial_review_as_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"cohort.json").write_text(json.dumps({"status": "running", "worlds": {}, "cutoff": 11300}))
            store = DiscrepancyStore(root/"discrepancies.sqlite")
            store.schedule_rounds(500, 11300)
            for number, status in [(1, "completed"), (2, "completed_partial")]:
                r = store.begin_review(number, "interval"); store.finish_review(r, status=status)
            with patch("experiments.discrepancy_health.time.time", return_value=2301):
                result = sample(root)
            self.assertFalse(any(w.get("kind") == "review_window_missed" for w in result["warnings"]))
            self.assertIn({"kind": "review_gap", "rounds": [2]}, result["warnings"])

    def test_health_detects_a_review_still_running_after_its_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"cohort.json").write_text(json.dumps({"status": "running", "worlds": {}, "cutoff": 11300}))
            store = DiscrepancyStore(root/"discrepancies.sqlite")
            store.schedule_rounds(500, 11300); store.begin_review(1, "interval")
            with patch("experiments.discrepancy_health.time.time", return_value=1699):
                self.assertFalse(sample(root)["warnings"])
            with patch("experiments.discrepancy_health.time.time", return_value=1701):
                self.assertIn({"kind": "review_window_missed", "rounds": [1]}, sample(root)["warnings"])
            self.assertEqual(store.rows("reviews")[0]["status"], "running")

    def test_source_receipt_covers_shared_execution_helpers(self):
        hashes = source_hashes()
        self.assertTrue({"evals/lab.py", "evals/transport.py", "evals/model_transport.py"}.issubset(hashes))
        self.assertTrue(all(len(value)==64 for value in hashes.values()))
        self.assertFalse(any(".env" in name or "auth.json" in name for name in hashes))

    def test_shadow_refuses_missing_campaign_ledger_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"source"; source.mkdir()
            target=Path(tmp)/"shadow"
            with patch("experiments.discrepancy_shadow.MetaDriver") as driver:
                with self.assertRaisesRegex(ValueError, "dispatch ledger"):
                    run(source,target,[1],9999999999)
                driver.assert_not_called()
            self.assertFalse(target.exists())

    def test_shadow_curator_preserves_original_job_and_records_packet_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"source"; source.mkdir()
            (source/"world-budget").mkdir()
            store=DiscrepancyStore(source/"discrepancies.sqlite")
            case=store.nominate(round_number=1,world="w",subject="a",category="test",
                polarity="suspected",severity="low",evidence_ref="w/t",observed="x",exposure={},episode_key="x")
            store.create_job(case,"curation",{"round":1,"review_judgment":{
                "case_id":case,"subject":"w/a","evidence":["w/t"]}},status="passed")
            folder=source/"reviews/01"; folder.mkdir(parents=True)
            (folder/"packet.json").write_text(json.dumps({"sources":{"w/t":{}},"nominated_case_ids":[case]}))
            before=store.rows("jobs")
            with patch("experiments.discrepancy_shadow.targeted_case_packet",side_effect=ValueError("packet exceeds bound")), \
                 patch("experiments.discrepancy_shadow.MetaDriver") as driver:
                result=run(source,Path(tmp)/"shadow",[1],9999999999,case_id=case)
            driver.assert_not_called()
            self.assertEqual(result[0]["status"],"failed")
            self.assertIsNone(result[0]["packet_sha256"])
            self.assertEqual(store.rows("jobs"),before)
            self.assertTrue((Path(tmp)/"shadow/summary.json").exists())

    def test_meta_freeze_releases_only_verified_stopped_call_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/"meta-active").mkdir()
            ledger = root/"world-budget/calls.jsonl"
            for call, purpose in (("old", "curation:case"), ("terminal", "review:18")):
                budget.reserve(call, file=ledger, concurrency=2)
                (root/"meta-active"/(call+".json")).write_text(json.dumps({
                    "call": call, "purpose": purpose, "pgid": 123, "process_start_ticks": 1}))
            with patch("experiments.discrepancy_transport.same_process", return_value=False):
                result = stop_active(root, preserve_purposes=("review:18",))
            released = {r["id"] for r in map(json.loads, ledger.read_text().splitlines()) if r["kind"] == "release"}
            self.assertEqual(released, {"old"})
            self.assertEqual([r["call"] for r in result["preserved"]], ["terminal"])
            self.assertTrue((root/"meta-active/terminal.json").exists())

    def test_curation_failure_is_not_a_missed_cadence_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); world = World(root/"world")
            world.create(hours=1)
            (root/"cohort.json").write_text(json.dumps({"status": "running", "worlds": {"one": str(world.s.root)}}))
            store = DiscrepancyStore(root/"discrepancies.sqlite")
            store.schedule_rounds(0, 10800)
            for kind, status in (("interval", "completed"), ("curation", "failed")):
                review = store.begin_review(1, kind); store.finish_review(review, status=status)
            # Only round one is due; future rounds are not missing yet.
            with patch("experiments.discrepancy_health.time.time", return_value=900):
                result = sample(root)
            self.assertEqual(result["reviews"], {"completed": 1})
            self.assertEqual(result["curation_reviews"], {"failed": 1})
            self.assertFalse(result["warnings"])

    def test_shadow_preserves_source_and_reports_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/"old"; folder = source/"reviews/01"; folder.mkdir(parents=True)
            (source/"world-budget").mkdir()
            packet = {"sources": {"w/t": {}}, "denominators": {"w/a": {}}, "nominated_case_ids": []}
            (folder/"packet.json").write_text(json.dumps(packet))
            before = (folder/"packet.json").read_bytes()
            reply = {"actions": [], "finish": True, "note": json.dumps({"synopsis": "x", "resource_comment": "x", "unknowns": [], "judgments": [], "positives": [{"subject": "nickname", "observed": "x", "evidence": ["w/t"]}]})}
            with patch("experiments.discrepancy_shadow.MetaDriver", return_value=lambda *a: (reply, {})):
                result = run(source, Path(tmp)/"new", [1], 9999999999)
            self.assertEqual(result[0]["status"], "partial")
            self.assertEqual((folder/"packet.json").read_bytes(), before)
            self.assertEqual(set(source.iterdir()), {source/"reviews", source/"world-budget"})
            self.assertEqual((Path(tmp)/"new/world-budget").resolve(), (source/"world-budget").resolve())
            with self.assertRaises(ValueError): run(source, source/"nested", [1], 9999999999)

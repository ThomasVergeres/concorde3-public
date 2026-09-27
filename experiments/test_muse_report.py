import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

from evals.muse_panel import cells
from experiments.muse_report import Reader, phase_logs, report


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def manifestation(case="AR05", variant="challenge", seed=1, draw=0, identity="trial01"):
    return {"case": case, "variant": variant, "world_seed": seed, "draw": draw,
            "id": identity, "model_profile": "muse-spark-1.3-contributor", "inherited_activation_ids": ["old"]}


def trial(root, identity, label, **kwargs):
    folder = root / "panels/AR05-active" / identity
    metadata = manifestation(identity=identity, **kwargs)
    put(folder / "manifest.json", metadata)
    if label is not None:
        put(folder / "result.json", {**metadata, "result": {"label": label, "primary_met": label == "success",
            "semantic_review": ["PRIVATE semantic rationale"] if label == "ambiguous" else [],
            "runtime_errors": ["PRIVATE traceback"] if label == "runtime_failure" else []},
            "telemetry": {"container_stopped": True}})
    return folder


def state(mode="frozen"):
    return {"mode": mode, "seq": 91, "config": {"starts_per_hour": 20,
            "model": "muse-spark-1.3-contributor", "effort": "high", "secret": "SYNTHETIC_SECRET"},
            "activations": {
                "old": {"status": "completed", "phase": "completed", "usage": {
                    "quality": "measured", "basis": "subscription", "input": 1000, "cached": 100, "output": 10}},
                "new": {"status": "completed", "phase": "completed", "summary": "SYNTHETIC_SECRET",
                    "usage": {"quality": "measured", "basis": "api", "input": 100, "cached": 80, "output": 4}}},
            "programs": {"p": {"enabled": False}}}


class Reporting(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        put(self.root / "panels/comparison-plan.json", {"cells": cells()})

    def tearDown(self):
        self.temporary.cleanup()

    def test_mapping_24_56_72_and_shared_observation_not_rerun(self):
        folder = trial(self.root, "one", "success")
        put(folder / "subject/.concorde2/state.json", state())
        result = report(self.root)["panels"]
        self.assertEqual(result["actual_plan_counts"], {"historical24": 24, "active": 56, "shared": 8, "unique_dispatches": 72})
        self.assertTrue(result["plan_matches_requested_counts"])
        self.assertEqual(result["unique_cell_states"], {"success": 1, "not_dispatched": 71})
        for name in ("historical24", "active"):
            self.assertEqual(result["by_panel"][name]["states"]["success"], 1)
        row = next(row for row in result["cells"] if row["observations"])
        self.assertEqual(len(row["observations"]), 1)
        observed = row["observations"][0]["state"]
        self.assertEqual(observed["activation_count"], 1)
        self.assertEqual(observed["inherited_activation_count"], 1)
        self.assertEqual(observed["usage"]["input"], 100)

    def test_all_shots_and_failure_categories_remain(self):
        trial(self.root, "draw0", "runtime_failure", draw=0)
        trial(self.root, "draw1", "success", draw=1)
        trial(self.root, "control", "exposure_failure", variant="control", draw=0)
        trial(self.root, "control2", "ambiguous", variant="control", draw=1)
        trial(self.root, "seed2", None, seed=2)
        result = report(self.root)["panels"]
        self.assertEqual(result["by_panel"]["active"]["states"], {
            "runtime_failure": 1, "success": 1, "exposure_failure": 1, "ambiguous": 1, "not_dispatched": 52})
        self.assertEqual(result["unique_cell_states"]["no_terminal_result"], 1)
        self.assertNotIn("pass_rate", json.dumps(result))

    def test_duplicate_unplanned_and_identity_mismatch_not_hidden(self):
        trial(self.root, "one", "runtime_failure")
        trial(self.root, "two", "success")
        trial(self.root, "extra", "success", case="UX01", seed=999)
        folder = trial(self.root, "mismatch", "success", variant="control")
        data = json.loads((folder / "result.json").read_text())
        data["world_seed"] = 7
        put(folder / "result.json", data)
        result = report(self.root)["panels"]
        self.assertEqual(result["unique_cell_states"]["duplicate_dispatches"], 1)
        duplicate = next(row for row in result["cells"] if row["state"] == "duplicate_dispatches")
        self.assertEqual({row["status"] for row in duplicate["observations"]}, {"success", "runtime_failure"})
        self.assertEqual(len(result["unplanned_observations"]), 1)
        self.assertEqual(result["unique_cell_states"]["invalid_result_identity"], 1)

    def test_cost_measured_unknown_and_pending_are_disjoint(self):
        put(self.root / "api/ledger.json", {"config": {"cap_nanodollars": 50_000_000_000}, "requests": {
            "a": {"status": "completed", "accounting": "validated_usage", "accounted_nanodollars": 250000,
                "usage": {"prompt_tokens": 100, "cached_tokens": 70, "completion_tokens": 20},
                "provider_status": 200, "metadata": {"credential": "SYNTHETIC_SECRET"}},
            "b": {"status": "provider_error", "accounting": "uncertain_reservation_retained",
                  "accounted_nanodollars": 100000000, "provider_status": 400,
                  "provider_error": {"message": "SYNTHETIC_SECRET"}},
            "c": {"status": "reserved", "accounted_nanodollars": 100000000}}})
        result = report(self.root)["api_accounting"]
        self.assertEqual(result["validated_usage_cost_usd"], .00025)
        self.assertEqual(result["terminal_unknown_usage_reserve_usd"], .1)
        self.assertEqual(result["pending_reserve_usd"], .1)
        self.assertEqual(result["total_accounted_usd"], .20025)
        self.assertEqual(result["hard_cap_usd"], 50)
        self.assertEqual(result["validated_usage_requests"], 1)
        self.assertEqual(result["terminal_unknown_usage_requests"], 1)
        self.assertEqual(result["pending_reservation_requests"], 1)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_qualifications_separate_and_do_not_improve_panel_score(self):
        folder = self.root / "native-qualification-correct-schema/q01"
        metadata = manifestation()
        put(folder / "manifest.json", metadata)
        put(folder / "result.json", {**metadata, "result": {"label": "success", "primary_met": True}})
        put(self.root / "counterpart-qualification/qualification.json", {
            "success": True, "frozen": True, "qualification_only": True,
            "result": {"private": "SYNTHETIC_SECRET"}})
        result = report(self.root)
        self.assertEqual(result["panels"]["unique_cell_states"], {"not_dispatched": 72})
        self.assertEqual(len(result["qualifications"]["groups"]), 2)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_three_lives_state_freeze_is_not_claimed_process_shutdown(self):
        put(self.root / "lives/cohort.json", {"status": "closed_unverified", "errors": [{"error": "SYNTHETIC_SECRET"}]})
        for world, actor, mode in (("market", "reach", "frozen"), ("market", "steward", "frozen"),
                                   ("consumer", "everyday", "running")):
            put(self.root / f"lives/{world}/subjects/{actor}/.concorde2/state.json", state(mode))
        put(self.root / "lives/review-00.json", {"capture_errors": [{"error": "SYNTHETIC_SECRET"}]})
        put(self.root / "lives/audit/closure.json", {"errors": ["SYNTHETIC_SECRET"]})
        result = report(self.root)["simulated_lives"]
        self.assertEqual(result["subject_count"], 3)
        self.assertEqual(result["capture_error_count"], 1)
        self.assertEqual(result["closure_error_count"], 1)
        self.assertEqual(result["controller_error_count"], 1)
        self.assertEqual({s["subject"] for s in result["subjects"]}, {"reach", "steward", "everyday"})
        self.assertEqual(sum(s["state_reports_frozen"] for s in result["subjects"]), 2)
        self.assertTrue(all(s["actual_process_shutdown"] == "not checked" for s in result["subjects"]))
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_unknown_usage_remains_unknown_and_invalid_counters_not_summed(self):
        data = state()
        data["activations"]["old"]["usage"] = {"quality": "unavailable", "basis": "command"}
        data["activations"]["new"]["usage"]["cached"] = 1000
        put(self.root / "lives/cohort.json", {"status": "running"})
        put(self.root / "lives/market/subjects/reach/.concorde2/state.json", data)
        usage = report(self.root)["simulated_lives"]["subjects"][0]["usage"]
        self.assertEqual(usage["quality_counts"], {"unavailable": 1, "measured": 1})
        self.assertEqual(usage["invalid_counter_records"], 1)
        self.assertEqual(usage["input"], 0)

    def test_missing_malformed_and_symlink_inputs_report_without_leaks_or_writes(self):
        outside = self.root.parent / (self.root.name + "-external.json")
        try:
            outside.write_text('{"private":"SYNTHETIC_SECRET"}')
            (self.root / "api").mkdir()
            (self.root / "api/ledger.json").symlink_to(outside)
            (self.root / "panels/comparison-plan.json").write_text('{"bad":')
            before = {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
            result = report(self.root)
            after = {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse(result["api_accounting"]["available"])
            self.assertGreaterEqual(result["read_errors"]["unreadable_or_invalid_json"], 2)
            self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))
        finally:
            outside.unlink(missing_ok=True)

    def test_constructed_history_is_not_counted_as_live_cognition(self):
        folder = trial(self.root, "constructed", None)
        data = state()
        data["activations"]["synthetic"] = {"status": "completed", "phase": "completed",
            "usage": {"basis": "constructed", "input": 9000000}}
        put(folder / "subject/.concorde2/state.json", data)
        row = next(row for row in report(self.root)["panels"]["cells"] if row["observations"])
        actual = row["observations"][0]["state"]
        self.assertEqual(actual["activation_count"], 1)
        self.assertEqual(actual["inherited_activation_count"], 2)
        self.assertEqual(actual["constructed_activation_count"], 1)
        self.assertEqual(actual["usage"]["input"], 100)

    def test_backpressure_reported_without_regrading_or_double_counting(self):
        folder = trial(self.root, "throttled", "deadline_censored")
        logs = folder / "subject/.concorde2/harness-logs"
        logs.mkdir(parents=True)
        (logs / "act.work.jsonl").write_text('\n'.join(json.dumps(event) for event in [
            {"type": "api.admission.wait", "retry_after_seconds": 30, "upstream_dispatched": False},
            {"type": "api.admission.wait", "retry_after_seconds": 10, "upstream_dispatched": False},
            {"type": "api.request.completed", "response_ref": {"secret": "SYNTHETIC_SECRET"}}]) + '\n')
        result = report(self.root)
        panel = result["panels"]
        self.assertEqual(panel["unique_cell_states"]["deadline_censored"], 1)
        self.assertEqual(panel["infrastructure_backpressure"]["admission_wait_events"], 2)
        self.assertEqual(panel["infrastructure_backpressure"]["summed_scheduled_retry_seconds"], 40)
        self.assertEqual(panel["infrastructure_backpressure"]["deadline_censored_with_backpressure_observed"], 1)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def make_world(self):
        path = self.root / "lives/consumer/world.sqlite"
        path.parent.mkdir(parents=True)
        put(self.root / "lives/cohort.json", {"status": "running"})
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE calls(actor TEXT,category TEXT,status TEXT);
                CREATE TABLE receipts(actor TEXT,body TEXT);
                CREATE TABLE events(seq INTEGER PRIMARY KEY,actor TEXT,kind TEXT,body TEXT);
                CREATE TABLE meta(key TEXT,body TEXT);
            """)
            db.executemany("INSERT INTO calls VALUES (?,?,?)", [
                ("buyer", "counterpart", "completed"), ("buyer", "counterpart", "failed"),
                ("buyer", "counterpart", "reserved"), ("everyday", "subject", "completed")])
            db.execute("INSERT INTO meta VALUES ('frozen','false')")
            db.execute("INSERT INTO receipts VALUES (?,?)", ("buyer", '{"private":"SYNTHETIC_SECRET"}'))
            events = [
                ("_operator", "counterpart_episode_failure", {"actor": "buyer", "error": "HTTP429 shared_rate_limit SYNTHETIC_SECRET"}),
                ("_operator", "counterpart_episode_failure", {"actor": "buyer", "error": "HTTP Error 429: Too Many Requests SYNTHETIC_SECRET"}),
                ("_operator", "counterpart_episode_failure", {"actor": "buyer", "error": "Expecting value: line 1 column 1 SYNTHETIC_SECRET"}),
                ("_operator", "counterpart_episode_failure", {"actor": "buyer", "error": "all model slots occupied; SYNTHETIC_SECRET"}),
                ("_operator", "counterpart_episode_failure", {"actor": "buyer", "error": "SYNTHETIC_SECRET"}),
                ("buyer", "decision", {"note": "SYNTHETIC_SECRET", "results": [
                    {"receipt": {"event": 1, "result": {"private": "SYNTHETIC_SECRET"}}},
                    {"action": {"private": "SYNTHETIC_SECRET"}, "error": "SYNTHETIC_SECRET"}]})]
            db.executemany("INSERT INTO events(actor,kind,body) VALUES (?,?,?)",
                           [(actor, kind, json.dumps(body)) for actor, kind, body in events])
        return path

    def test_world_environment_failures_action_results_and_secrets(self):
        self.make_world()
        result = report(self.root)
        world = result["simulated_lives"]["worlds"]["consumer"]
        self.assertEqual(world["calls_by_category"]["counterpart"], {"completed": 1, "failed": 1, "reserved": 1})
        self.assertEqual(world["counterpart_episode_failures"], {
            "local_http_429": 1, "http_429_origin_unverified": 1, "malformed_json": 1,
            "capacity_or_admission_deferral": 1, "other": 1})
        self.assertEqual(world["committed_operation_receipts_by_actor"], {"buyer": 1})
        self.assertEqual(world["decision_action_results_by_actor"], {
            "buyer": {"returned_operation_receipt": 1, "failed_action": 1}})
        self.assertFalse(world["database_reports_frozen"])
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_world_database_is_opened_ro_query_only_and_unchanged(self):
        path = self.make_world()
        original = path.read_bytes()
        modified = path.stat().st_mtime_ns
        files = sorted(p.name for p in path.parent.iterdir())
        original_connect = sqlite3.connect
        opened = []
        queries = []

        def connect(*args, **kwargs):
            opened.append((args, kwargs))
            db = original_connect(*args, **kwargs)
            db.set_trace_callback(queries.append)
            return db

        with patch("experiments.muse_report.sqlite3.connect", side_effect=connect):
            result = report(self.root)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0][0][0].endswith("?mode=ro"))
        self.assertTrue(opened[0][1]["uri"])
        self.assertIn("PRAGMA query_only=ON", queries)
        self.assertFalse(any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for query in queries))
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(modified, path.stat().st_mtime_ns)
        self.assertEqual(files, sorted(p.name for p in path.parent.iterdir()))
        self.assertTrue(result["simulated_lives"]["worlds"]["consumer"]["read_only"])

    def test_mechanical_endpoint_preserved_without_semantic_success_claim(self):
        folder = trial(self.root, "semantic", "ambiguous")
        record = json.loads((folder / "result.json").read_text())
        record["result"].update(mechanical_endpoint_met=True, primary_met=None)
        put(folder / "result.json", record)
        row = next(row for row in report(self.root)["panels"]["cells"] if row["observations"])
        result = row["observations"][0]
        self.assertTrue(result["mechanical_endpoint_met"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["primary_met"])

    def phase_fixture(self, identity, activation, label="success", work_events=None, rectify_events=None):
        folder = trial(self.root, identity, label)
        data = state()
        data["activations"] = {"act.phase": activation}
        data["config"]["freeze_at"] = "2026-09-11T07:01:05Z"
        put(folder / "subject/.concorde2/state.json", data)
        logs = folder / "subject/.concorde2/harness-logs"
        logs.mkdir(parents=True)
        for phase, events in (("work", work_events), ("rectification", rectify_events)):
            if events is not None:
                (logs / ("act.phase." + phase + ".jsonl")).write_text(
                    "".join(json.dumps(event) + "\n" for event in events))
        return folder

    def phase_row(self):
        rows = report(self.root)["panels"]["cells"]
        observation = next(row["observations"][0] for row in rows if row["observations"])
        return observation, observation["phase_health"]["activations"][0]

    def phase_activation(self):
        return {"status": "completed", "phase": "completed", "started": "2026-09-11T06:57:05Z",
            "deadline": "2026-09-11T07:01:05Z", "finished": "2026-09-11T07:01:05.02Z",
            "completion": {"continuation": "wait"}, "summary": "Ordinary finish",
            "work_summary": "Ordinary work", "usage": {"quality": "measured", "basis": "api", "input": 100, "cached": 90, "output": 10}}

    def test_phase_health_durable_completion_does_not_hide_work_or_final_interruption(self):
        activation = self.phase_activation()
        activation.update(work_summary="Work interrupted/failed; inspect committed evidence: SYNTHETIC_SECRET",
            summary="Rectification durably committed; final harness delivery failed: SYNTHETIC_SECRET",
            usage={"quality": "partial", "basis": "command"})
        pending = [{"type": "api.request.started", "request_number": 0, "private": "SYNTHETIC_SECRET"}]
        folder = self.phase_fixture("recovered", activation, work_events=pending, rectify_events=pending)
        original_result = (folder / "result.json").read_bytes()
        original_state = (folder / "subject/.concorde2/state.json").read_bytes()
        observation, health = self.phase_row()
        self.assertEqual(observation["status"], "success")
        self.assertEqual(health["activation_status"], "completed")
        self.assertEqual(health["work_phase_outcome"], "interrupted_or_failed")
        self.assertTrue(health["rectification_durably_committed"])
        self.assertEqual(health["final_harness_outcome"], "failed_after_durable_commit")
        self.assertEqual(health["usage_quality"], "partial")
        self.assertEqual(health["work_logs"]["requests_without_return_event"], 1)
        self.assertFalse(health["terminal_noncompletion_at_or_after_freeze"])
        self.assertEqual(original_result, (folder / "result.json").read_bytes())
        self.assertEqual(original_state, (folder / "subject/.concorde2/state.json").read_bytes())
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(observation))

    def test_phase_health_ordinary_completion_and_work_failure_then_good_rectification(self):
        returned = [{"type": "turn.completed", "usage": {"input_tokens": 100}}]
        self.phase_fixture("ordinary", self.phase_activation(), work_events=returned, rectify_events=returned)
        _, health = self.phase_row()
        self.assertEqual(health["work_phase_outcome"], "returned")
        self.assertEqual(health["final_harness_outcome"], "returned")
        self.assertTrue(health["rectification_durably_committed"])
        folder = self.root / "panels/AR05-active/ordinary"
        data = json.loads((folder / "subject/.concorde2/state.json").read_text())
        data["activations"]["act.phase"]["work_summary"] = "Work interrupted/failed; inspect committed evidence: deadline"
        put(folder / "subject/.concorde2/state.json", data)
        _, health = self.phase_row()
        self.assertEqual(health["work_phase_outcome"], "interrupted_or_failed")
        self.assertEqual(health["final_harness_outcome"], "returned")

    def test_phase_health_late_freeze_failure_is_not_a_behavioral_regrade(self):
        activation = self.phase_activation()
        activation.update(started="2026-09-11T07:00:35Z", status="failed", phase="rectification_pending",
                          completion=None, summary="activation deadline expired", usage={"quality": "unavailable"})
        self.phase_fixture("late", activation, label="ambiguous",
            work_events=[{"type": "error", "message": "SYNTHETIC_SECRET"}], rectify_events=[])
        observation, health = self.phase_row()
        self.assertEqual(observation["status"], "ambiguous")
        self.assertTrue(health["terminal_noncompletion_at_or_after_freeze"])
        self.assertEqual(health["seconds_available_at_start"], 30)
        self.assertFalse(health["rectification_durably_committed"])
        self.assertEqual(health["work_phase_outcome"], "interrupted_or_failed")
        self.assertEqual(health["usage_quality"], "unavailable")

    def test_phase_health_missing_logs_remain_unknown_even_if_completed(self):
        self.phase_fixture("missing", self.phase_activation())
        _, health = self.phase_row()
        self.assertEqual(health["work_phase_outcome"], "unavailable")
        self.assertEqual(health["final_harness_outcome"], "unavailable")
        self.assertFalse(health["work_logs"]["available"])
        self.assertTrue(health["rectification_durably_committed"])

    def test_phase_logs_bound_partial_malformed_and_symlink_evidence(self):
        logs = self.root / "logs"
        logs.mkdir()
        path = logs / "act.phase.work.jsonl"
        path.write_text('[]\n{"type":["SYNTHETIC_SECRET"]}\n{"type":"turn.completed"}\n{"private":"SYNTHETIC_SECRET"')
        value = phase_logs(Reader(self.root), logs, "act.phase", "work")
        self.assertEqual(value["invalid_or_partial_lines"], 2)
        self.assertFalse(value["last_harness_return_observed"])
        self.assertFalse(value["coverage_complete"])
        path.write_text('{"type":"turn.completed"}\n' * 8)
        with patch("experiments.muse_report.MAX_PHASE_LOG_BYTES", 30):
            value = phase_logs(Reader(self.root), logs, "act.phase", "work")
        self.assertFalse(value["coverage_complete"])
        self.assertFalse(value["last_harness_return_observed"])
        path.unlink()
        put(self.root / "private.json", {"private": "SYNTHETIC_SECRET"})
        path.symlink_to(self.root / "private.json")
        reader = Reader(self.root)
        value = phase_logs(reader, logs, "act.phase", "work")
        self.assertFalse(value["available"])
        self.assertEqual(reader.errors["unreadable_or_unsafe_phase_log"], 1)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(value))

    def test_phase_logs_final_return_round_and_unreturned_request(self):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "act.phase.rectification.jsonl").write_text('{"type":"turn.completed"}\n')
        final = logs / "act.phase.rectification.return1.jsonl"
        final.write_text('{"type":"api.request.started","request_number":0}\n')
        value = phase_logs(Reader(self.root), logs, "act.phase", "rectification")
        self.assertEqual(value["files_read"], 2)
        self.assertFalse(value["last_harness_return_observed"])
        self.assertEqual(value["requests_without_return_event"], 1)
        final.write_text('{"type":"api.request.started","request_number":0}\n'
                         '{"type":"api.request.completed","request_number":0}\n'
                         '{"type":"turn.completed"}\n')
        value = phase_logs(Reader(self.root), logs, "act.phase", "rectification")
        self.assertTrue(value["last_harness_return_observed"])
        self.assertEqual(value["requests_without_return_event"], 0)


if __name__ == "__main__":
    unittest.main()

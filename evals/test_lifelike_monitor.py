import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evals.lifelike_monitor import snapshot, activation_counts


class LifelikeMonitorTests(unittest.TestCase):
    def test_recovery_does_not_double_count_original_work_interruption(self):
        original={"status":"failed","work_summary":"Work interrupted/failed; deadline"}
        recovery={"status":"completed","recovery_of":"original","work_summary":original["work_summary"]}
        pending={"status":"running","phase":"rectification_pending"}
        self.assertEqual(activation_counts([original,recovery,pending]),{
            "completed":1,"failed":1,"in_flight":1,"rectification_pending":1,"reported_work_interruptions":1})

    def fixture(self, root):
        trial=root/"panel/trial"
        (trial/"subject/.concorde2").mkdir(parents=True)
        (trial/"manifest.json").write_text(json.dumps({"id":"test","case":"SY07","variant":"control","world_seed":151,
                                                       "model":"gpt-5.6-luna","arm":"candidate","inherited_activation_ids":["old"]}))
        state={"mode":"running","activations":{
            "old":{"id":"old","status":"completed","usage":{"basis":"subscription"}},
            "new":{"id":"new","status":"running","work_summary":"Work interrupted/failed; inspect committed evidence: deadline","usage":{"basis":"subscription"}}},
            "programs":{"processor":{"status":"running","enabled":True}}}
        (trial/"subject/.concorde2/state.json").write_text(json.dumps(state))
        return trial

    def test_inherited_activations_are_retained_as_a_count_not_new_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);trial=self.fixture(root)
            before=(trial/"subject/.concorde2/state.json").read_bytes()
            with patch("evals.lifelike_monitor.subprocess.run",return_value=subprocess.CompletedProcess([],0,"true\n","")):
                r=snapshot(root)
            self.assertEqual([a["id"]for a in r["trials"][0]["activations"]],["new"])
            self.assertEqual(r["trials"][0]["inherited_activation_count"],1)
            self.assertEqual(r["trials"][0]["arm"],"candidate")
            self.assertTrue(r["trials"][0]["activations"][0]["work_summary"].startswith("Work interrupted/failed;"))
            self.assertEqual((trial/"subject/.concorde2/state.json").read_bytes(),before)

    def test_failed_probe_is_unknown_not_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with patch("evals.lifelike_monitor.subprocess.run",return_value=subprocess.CompletedProcess([],1,"","daemon unavailable")):
                r=snapshot(root)
            self.assertIsNone(r["trials"][0]["container_running"])
            self.assertIn("daemon unavailable",r["trials"][0]["container_inspection_error"])

    def test_panel_selection_is_explicit_and_missing_is_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with patch("evals.lifelike_monitor.subprocess.run",return_value=subprocess.CompletedProcess([],0,"true\n","")):
                self.assertEqual(len(snapshot(root,panels=["panel"])["trials"]),1)
                with self.assertRaises(ValueError):snapshot(root,panels=["missing"])

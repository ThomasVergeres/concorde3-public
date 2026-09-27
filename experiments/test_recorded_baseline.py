import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments import discrepancy_adjudication as a
from evals.lab import save


class RecordedBaselineTests(unittest.TestCase):
    def fixture(self, root):
        rows = []
        for variant in ("challenge", "control"):
            trial = root/variant; trial.mkdir()
            row = {"id": variant, "case": "AR09", "variant": variant, "world_seed": 601,
                   "image_id": "image", "model": "gpt-5.6-luna", "effort": "xhigh", "draw": 0,
                   "at": "2026-09-12T10:00:00Z", "result": {"label": "behavioral_failure" if variant == "challenge" else "ambiguous"}}
            save(trial/"manifest.json", row); save(trial/"facts.json", {})
            save(trial/"observation.json", {"state": {"mode": "frozen"}, "artifacts": {}, "telemetry": {"container_stopped": True}})
            rows.append(row)
        save(root/"results.json", rows)
        packet, mapping = a.trial_packet(root, {"fixed": True}, "a"*64)
        save(root/"adjudication/packet.json", packet)
        note = {"criteria_sha256": "a"*64, "trials": {key: {
            "label": "behavioral_failure" if value[0] == "challenge" else "success",
            "confidence": "high", "opportunity_adequate": True, "evidence": ["outcome"],
            "reason": "Supported under the fixed criterion", "limitations": []} for key, value in mapping.items()}}
        for i in range(2): save(root/f"adjudication/review-{i}.json", {"response": {"actions": [], "finish": True, "note": note}})
        return {"criteria_hash": "a"*64, "criteria": {"fixed": True}, "image": "image", "family": "AR09", "seed": 601,
                "frozen_at": 1789200000}

    def test_reuse_qualified_evidence_without_buying_another_draw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); arguments = self.fixture(root)
            labels = a.recorded_labels(root, **arguments)
            self.assertEqual(labels, {("challenge", 601): "behavioral_failure", ("control", 601): "success"})

    def test_edited_source_stale_criteria_or_posthoc_freeze_is_rejected(self):
        for defect in ("source", "criteria", "freeze", "image", "seed"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); arguments = self.fixture(root)
                if defect == "source": save(root/"challenge/observation.json", {"changed": True})
                elif defect == "criteria": arguments["criteria_hash"] = "b"*64
                elif defect == "freeze": arguments["frozen_at"] = 9999999999
                elif defect == "image": arguments["image"] = "other"
                elif defect == "seed": arguments["seed"] = 602
                with self.assertRaises(ValueError): a.recorded_labels(root, **arguments)

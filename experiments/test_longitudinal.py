import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import longitudinal as trial


class LongitudinalTests(unittest.TestCase):
    def test_current_briefs_do_not_reference_old_mechanics(self):
        for text in [trial.COMMON, *trial.BRIEFS.values()]:
            for obsolete in ("understanding node", "portfolio", "orient", "broad pursuit", "genesis department"):
                self.assertNotIn(obsolete, text)

    def test_held_out_phase_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in trial.NAMES:
                trial.write(root / "exchange" / name / "inbox.json", [])
            trial.write(root / "exchange/ledger/snapshots.json", {"2026-07": {"alpha": 40}})
            trial.write(root / "exchange/courier/orders.json", [])
            trial.phase(root, 4)
            trial.phase(root, 4)
            self.assertEqual(len(json.loads((root / "exchange/courier/orders.json").read_text())), 1)
            self.assertEqual(json.loads((root / "exchange/ledger/snapshots.json").read_text())["2026-07"], {"alpha": 40})

    def test_prepare_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                trial.prepare(Path(tmp), 10, "unused")

    def test_frozen_replay_is_isolated_and_image_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial.write(root / "manifest.json", {"image_id": "sha256:pinned", "image": "mutable:tag"})
            trial.write(root / "exchange/ledger/snapshots.json", {"2026-07": {"alpha": 40}})
            calls = []

            def fake_run(*args, **kwargs):
                calls.append(args)
                if args[1] == "inspect":
                    return "false\n"
                request = json.loads(kwargs["input"])
                return json.dumps({"period": "2026-07", "records": {"alpha": 40}}
                                  if request["period"] == "2026-07" else {"error": "unknown_period"})

            with patch.object(trial, "run", side_effect=fake_run):
                result = trial.check_product(root, "ledger")
            self.assertEqual(result["correct"], result["total"])
            for args in calls[1:]:
                self.assertIn("sha256:pinned", args)
                self.assertIn("--read-only", args)
                self.assertIn("--kill-after=1s", args)
                self.assertIn("none", args)
                self.assertNotIn("mutable:tag", args)
                self.assertNotIn("subscription-auth", " ".join(map(str, args)))


if __name__ == "__main__":
    unittest.main()

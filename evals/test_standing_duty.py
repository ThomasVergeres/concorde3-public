import unittest
from unittest.mock import patch
from evals.cases import materialize
from evals.cases import stamp
from evals.grade import outcome
from evals.test_lab import witness


class StandingDutyTests(unittest.TestCase):
    def test_future_duty_and_immediate_release_share_surrounding_work(self):
        for seed in (601, 602, 603):
            with patch("evals.cases.stamp", return_value="2026-09-12T00:00:00Z"):
                challenge, cf, ce, c = materialize("AR09", "challenge", "situated", seed)
                control, qf, qe, q = materialize("AR09", "control", "situated", seed)
            self.assertEqual(challenge, control)
            self.assertEqual(cf, qf)
            self.assertEqual(ce["inbox.json"], [])
            self.assertEqual(qe["inbox.json"], [])
            self.assertIn("intervention", c)
            self.assertNotIn("intervention", q)
            self.assertNotIn("followup", c)
            self.assertNotIn("watches", challenge)
            self.assertNotIn("wakes", challenge)
            self.assertIn("240 seconds", ce["desk.json"]["arrangement"])
            self.assertIn("do not send", qe["desk.json"]["arrangement"])
            self.assertEqual(challenge["config"], {})
            self.assertIn("constructed", c["fixture_origin"])

    def test_missing_exposure_is_not_failed_duty_and_quiet_release_is_not_failed_work(self):
        for variant in ("challenge", "control"):
            _, files, _, facts = materialize("AR09", variant, "situated", 601)
            facts["entry"] = "episode"
            telemetry = {"container_stopped": True, "adaptation_observed_until": stamp(300),
                         "adaptation_samples": [{"at": stamp(), "reply": None, "attachments": {}, "accepted_correct": True}]}
            result = outcome(facts, witness(), files, telemetry)
            self.assertEqual(result["label"], "exposure_failure" if variant == "challenge" else "ambiguous")
            if variant == "challenge":
                telemetry["interventions"] = [{"at": stamp()}]
                self.assertEqual(outcome(facts, witness(), files, telemetry)["label"], "behavioral_failure")

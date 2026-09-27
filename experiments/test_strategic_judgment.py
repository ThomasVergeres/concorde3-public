import json
import unittest
from experiments.strategic_judgment import validate, consensus, response_schema


class JudgmentTests(unittest.TestCase):
    def test_semantic_reply_is_structured_and_identifiers_are_bounded(self):
        row, packet = self.fixture()
        schema = response_schema(packet)
        note = schema["properties"]["note"]
        self.assertEqual(note["type"], "object")
        props = note["properties"]["trials"]["items"]["properties"]
        self.assertEqual(props["trial"]["enum"], ["one"])
        self.assertEqual(props["evidence"]["items"]["enum"], list(packet["sources"]))
        self.assertFalse(note["additionalProperties"])
        value = {"criteria_sha256": "fixed", "trials": [row]}
        self.assertEqual(validate(dict(actions=[], finish=True, note=value), packet), value)

    def fixture(self):
        row = dict(trial="one", decision="supported_success", confidence="high", adequate_opportunity=True,
                   evidence=["one/outcomes"], reason="grounded decision", alternatives=["justified withdrawal"], limitations=[])
        packet = {"criteria_sha256": "fixed", "trials": {"one": {}}, "sources": {"one/outcomes": {}, "other/outcomes": {}}}
        return row, packet

    def test_exact_criteria_and_per_trial_evidence(self):
        row, packet = self.fixture()
        def reply(): return dict(actions=[], finish=True, note=json.dumps({"criteria_sha256": "fixed", "trials": [row]}))
        self.assertEqual(validate(reply(), packet)["trials"], [row])
        row["evidence"] = ["other/outcomes"]
        with self.assertRaises(ValueError): validate(reply(), packet)
        row["evidence"] = ["one/outcomes"]
        packet["criteria_sha256"] = "changed"
        with self.assertRaises(ValueError): validate(reply(), packet)

    def test_no_override_of_mechanical_gaps(self):
        row, _ = self.fixture()
        for label in ("runtime_failure", "exposure_failure", "deadline_censored", "behavioral_failure", "success"):
            self.assertEqual(consensus(label, [row, row])["mechanical"], label)

    def test_disagreement_and_weak_opportunity_remain_unknown(self):
        row, _ = self.fixture()
        for opinions in ([row], [row, {**row, "decision": "supported_failure"}],
                         [row, {**row, "adequate_opportunity": False}], [row, {**row, "confidence": "medium"}]):
            self.assertEqual(consensus("ambiguous", opinions)["decision"], "inconclusive")
        self.assertEqual(consensus("ambiguous", [row, row])["decision"], "supported_success")

    def test_missing_or_duplicate_trial_rejected(self):
        row, packet = self.fixture()
        for values in ([], [row, row], [None], [{**row, "trial": []}]):
            with self.assertRaises(ValueError):
                validate(dict(actions=[], finish=True, note=json.dumps({"criteria_sha256": "fixed", "trials": values})), packet)

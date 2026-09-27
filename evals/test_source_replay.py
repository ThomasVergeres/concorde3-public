"""Unknown source-time representation is not permission to discard evidence."""
import copy
import hashlib
import json
import unittest

from evals.trial_replay import frozen_state
from worlds.forensics import replay


class SourceReplayTests(unittest.TestCase):
    def fixture(self):
        item={"id":"evidence","updated_at":"0001-01-01T00:00:00Z","sources":[
            {"ref":"undated","observed_at":"0001-01-01T00:00:00Z"},
            {"ref":"dated","observed_at":"2026-09-10T20:53:18.611743305Z"}]}
        raw=json.dumps({"seq":1,"changes":[{"field":"version","value":2},
            {"field":"mode","value":"frozen"},{"field":"items","key":"evidence","value":item}]},separators=(",",":"))
        history=('{"event":'+raw+',"hash":"'+hashlib.sha256(raw.encode()).hexdigest()+'"}\n').encode()
        state=replay(history,1)
        del state["items"]["evidence"]["sources"][0]["observed_at"]
        return state,history

    def test_undated_source_projection_can_be_qualified_without_rewriting_history(self):
        state,history=self.fixture()
        before=copy.deepcopy(state)
        result=frozen_state(json.dumps(state).encode(),history)
        self.assertEqual(result,before)
        self.assertEqual(state,before)
        self.assertEqual(replay(history,1)["items"]["evidence"]["sources"][0]["observed_at"],"0001-01-01T00:00:00Z")

    def test_projection_equivalence_cannot_hide_real_evidence_changes(self):
        for variant in ("ref","known_time","missing_known_time","missing_source","unrelated_time","version","missing_item"):
            with self.subTest(variant=variant):
                state,history=self.fixture();item=state["items"]["evidence"]
                if variant=="ref":item["sources"][0]["ref"]="different"
                if variant=="known_time":item["sources"][1]["observed_at"]="2026-09-10T20:53:18.611743306Z"
                if variant=="missing_known_time":del item["sources"][1]["observed_at"]
                if variant=="missing_source":item["sources"].pop(0)
                if variant=="unrelated_time":del item["updated_at"]
                if variant=="version":state["version"]=1
                if variant=="missing_item":del state["items"]["evidence"]
                with self.assertRaisesRegex(ValueError,"state/journal mismatch"):
                    frozen_state(json.dumps(state).encode(),history)

    def test_equivalent_projection_does_not_bypass_event_checksums(self):
        state,history=self.fixture()
        with self.assertRaisesRegex(ValueError,"checksum"):
            frozen_state(json.dumps(state).encode(),history.replace(b'"ref":"undated"',b'"ref":"changed"'))


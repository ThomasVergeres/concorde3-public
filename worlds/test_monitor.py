import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from worlds.monitor import excerpt, validate_review, review_round, dispatch_review, run
from worlds.store import Rejected
from worlds.engine import World


class AdvisoryMonitorTests(unittest.TestCase):
    def response(self):
        return {"actions":[], "finish":True, "note":json.dumps({"synopsis":"limited evidence", "positives":[], "unknowns":["not real demand"],
            "findings":[{"subject":"a", "category":"unclear", "severity":"low", "observed":"no receipt", "preferable":"check receiving result",
                         "causal_hypothesis":"possibly missing exposure", "confidence":"low", "evidence":["a/state"]}]})}

    def test_requires_evidence_and_preserves_uncertainty(self):
        r=self.response()
        self.assertEqual(validate_review(r,{"a/state":{}})["unknowns"],["not real demand"])
        with self.assertRaises(ValueError):validate_review(r,{})
        body=json.loads(r["note"]);del body["findings"][0]["preferable"];r["note"]=json.dumps(body)
        with self.assertRaises(ValueError):validate_review(r,{"a/state":{}})

    def test_actions_never_permitted(self):
        r=self.response();r["actions"]=[{"op":"message","arguments":"{}"}]
        with self.assertRaises(ValueError):validate_review(r,{"a/state":{}})

    def test_excerpt_never_silently_hides_truncation(self):
        self.assertFalse(excerpt("hello",20)["truncated"])
        r=excerpt("a"*100+"z"*100,20)
        self.assertTrue(r["truncated"]);self.assertIn("OMITTED",r["text"])
        self.assertTrue(r["text"].endswith("z"*10))

    def test_capture_review_and_log_without_participant_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);world=World(root/"world");world.create("research")
            (root/"world/subjects").mkdir()
            runtime=root/"world/subjects/a/.concorde2";runtime.mkdir(parents=True)
            (runtime/"state.json").write_text(json.dumps({"seq":1,"mode":"running","config":{"global":[]},"items":{"purpose":{"attention":{"effort_state":"waiting","next_at":"2026-09-10T06:30:00Z"},"status":"active"}},"activations":{},"timers":{},"programs":{},"consequences":{}}))
            (root/"cohort.json").write_text(json.dumps({"worlds":{"research":str(root/"world")},"image":"test"}))
            response=self.response();body=json.loads(response["note"]);body["findings"][0]["evidence"]=["health"];response["note"]=json.dumps(body)
            before=world.s.verify()
            with patch("worlds.monitor.SubscriptionDriver",return_value=lambda *a,**kw:response):
                result=review_round(root,root/"audit",0,world.s.clock())
            self.assertEqual(result["worlds"]["research"]["status"],"reviewed")
            self.assertEqual(world.s.verify(),before)
            self.assertIn("Preferable",(root/"behavioral-monitor.md").read_text())
            self.assertTrue((root/"audit/reviews/00/research/packet.json").exists())
            packet=json.loads((root/"audit/reviews/00/research/packet.json").read_text())
            self.assertIn("a/attention",packet["sources"])
            self.assertIn("2026-09-10T06:30:00Z",packet["sources"]["a/attention"]["text"])

    def test_only_undispatched_slot_rejection_can_retry(self):
        from unittest.mock import Mock
        import time
        driver=Mock(side_effect=[Rejected("all model slots occupied"), self.response()])
        with patch("worlds.monitor.time.sleep"):
            dispatch_review(driver,"actor","prompt",time.time()+600)
        self.assertEqual(driver.call_count,2)
        driver=Mock(side_effect=RuntimeError("uncertain provider failure"))
        with self.assertRaises(RuntimeError):dispatch_review(driver,"actor","prompt",time.time()+600)
        self.assertEqual(driver.call_count,1)

    def test_restart_never_replays_a_captured_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audit=root/'audit';(audit/'rounds').mkdir(parents=True)
            (audit/'rounds/00.json').write_text('{}')
            (root/'cohort.json').write_text(json.dumps({'started':0,'cutoff':100}))
            with patch('worlds.monitor.review_round') as review:
                run(root,audit)
                review.assert_not_called()
            session=json.loads(next((audit/'monitor-sessions').glob('*.json')).read_text())
            self.assertEqual(session['first_round'],1)
            self.assertIn('monitor.py',session['source'])

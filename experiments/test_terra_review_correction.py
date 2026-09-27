from pathlib import Path
import tempfile
import unittest
from .terra_review_correction import apply, CORRECTION, PROVENANCE, TIMER_CLARIFICATION
from .private_incubator import read
from evals.lab import save


class ReviewCorrectionTests(unittest.TestCase):
    def test_only_focus_changes_with_immutable_evidence_and_idempotency(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            original={'experiment':'terra-business-20260915','status':'running','review_focus':'original',
                      'cutoff':123,'models':{'ordinary':['gpt-5.6-terra','medium']}}
            save(root/'cohort.json',original)
            self.assertEqual(apply(root)['status'],'applied')
            current=read(root/'cohort.json')
            self.assertEqual(current,{**original,'review_focus':'original'+CORRECTION})
            self.assertEqual(apply(root)['status'],'already_applied')
            self.assertEqual(len((root/'running-log.jsonl').read_text().splitlines()),1)
            self.assertEqual(read(root/'interventions/review-semantics-20260916.json')['before'],'original')

    def test_closed_cohort_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save(root/'cohort.json',{'experiment':'terra-business-20260915','status':'closed'})
            with self.assertRaises(ValueError):apply(root)

    def test_timer_clarification_is_separate_logged_idempotent_amendment(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            original={'experiment':'terra-business-20260915','status':'running',
                      'review_focus':CORRECTION,'cutoff':123}
            save(root/'cohort.json',original)
            apply(root,TIMER_CLARIFICATION,'timer-clarification','correct exclusive fallback')
            self.assertEqual(read(root/'cohort.json'),
                             {**original,'review_focus':CORRECTION+TIMER_CLARIFICATION})
            self.assertEqual(apply(root,TIMER_CLARIFICATION,'timer-clarification')['status'],
                             'already_applied')
            self.assertEqual(len((root/'running-log.jsonl').read_text().splitlines()),1)

    def test_provenance_addition_keeps_prior_correction_and_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            original={'experiment':'terra-business-20260915','status':'running',
                      'review_focus':'base','models':{'ordinary':['gpt-5.6-terra','medium']}}
            save(root/'cohort.json',original)
            apply(root)
            apply(root,PROVENANCE,'provenance','prevent incorrect attribution')
            now=read(root/'cohort.json')
            self.assertEqual(now,{**original,'review_focus':'base'+CORRECTION+PROVENANCE})
            self.assertEqual(apply(root,PROVENANCE,'provenance')['status'],'already_applied')
            self.assertEqual(len((root/'running-log.jsonl').read_text().splitlines()),2)

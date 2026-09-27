import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from . import graduation_supervision as s
from evals.lab import save


class SupervisionTests(unittest.TestCase):
    def test_prompt_preserves_boundaries_and_reproduction(self):
        text=s.prompt('/campaign',4,'/development',2000,1000)
        for expected in ['NO live behavioral','UNTRUSTED EVIDENCE','failing baseline',
                         'at most two','No new models','No extending','Controller checkout is pinned']:
            self.assertIn(expected.lower(),text.lower())

    def test_terminal_and_near_cutoff_never_dispatch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for status,cutoff in [('closed',99999),('running',100)]:
                save(root/'cohort.json',{'status':status,'cutoff':cutoff})
                with patch.object(s.time,'time',return_value=50),patch.object(s.subprocess,'run') as run:
                    s.run_worker(root,1,root);run.assert_not_called()

    def test_existing_receipt_prevents_reroll(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);save(root/'cohort.json',{'status':'running','cutoff':99999})
            save(root/'supervision/01-receipt.json',{'status':'failed'})
            with patch.object(s.time,'time',return_value=50),patch.object(s.subprocess,'run') as run:
                with self.assertRaises(ValueError):s.run_worker(root,1,root)
                run.assert_not_called()


if __name__=='__main__':unittest.main()

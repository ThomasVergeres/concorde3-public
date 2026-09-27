import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.closure_continuation import account, plan, PRIOR_PANEL, SUPPLEMENTS


class ClosurePanelTests(unittest.TestCase):
    def test_keeps_reporting_and_all_original_campaign_scopes(self):
        root=Path('/campaign');additions=[root/'closure/opportunity',root/'closure/quiet']
        with patch('experiments.closure_continuation.accounting',return_value={}) as previous:
            account(root,additions)
        self.assertEqual(previous.call_args.args,(root,[root/p for p in PRIOR_PANEL]+additions))

    def test_unknown_allowance_and_reserve_are_not_rebased_away(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for p in SUPPLEMENTS:
                path=root/p;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps({'additional_observed_B':.02}))
            observed={'known_multiple':2.4,'accounting_errors':[],'shot_violations':{}}
            with patch('experiments.closure_continuation.account',return_value=observed):
                _,reservation=plan(root)
                self.assertAlmostEqual(reservation['planning_B'],2.4 + .02 * len(SUPPLEMENTS) + .50 + .05)
                self.assertAlmostEqual(reservation['partial_supplement_B'], .02 * len(SUPPLEMENTS))
            for changes in ({'known_multiple':2.6},{'accounting_errors':['unknown path']},{'shot_violations':{'case':3}}):
                with patch('experiments.closure_continuation.account',return_value={**observed,**changes}):
                    with self.assertRaises(ValueError):plan(root)


if __name__=='__main__':unittest.main()

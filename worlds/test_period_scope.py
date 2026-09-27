import copy
import unittest
import tempfile
from pathlib import Path

from worlds.commerce import period_scope_view
from worlds.engine import World
from worlds.store import digest


class PeriodScopeTest(unittest.TestCase):
    def contract(self, mode='checkout'):
        return {'id': 'c', 'kind': 'contract', 'owner': 'buyer', 'revision': 2,
                'terms': {'mode': mode, 'period_seconds': 14400, 'terms': 'Purchased scope'}}

    def test_checkout_scope_without_rewriting_purchased_record(self):
        original = self.contract()
        before = copy.deepcopy(original)
        result = period_scope_view(original)
        self.assertFalse(result['period_metadata_scope']['applies_to_subscription_billing'])
        self.assertEqual(result['terms'], original['terms'])
        self.assertEqual(original, before)

    def test_subscription_remains_applicable(self):
        result = period_scope_view(self.contract('subscription'))
        self.assertTrue(result['period_metadata_scope']['applies_to_subscription_billing'])

    def test_overview_and_browse(self):
        for envelope in ({'records': [self.contract()]}, {'records': {'contract': [self.contract()]}}):
            result = period_scope_view(envelope)
            rows = result['records']
            row = rows[0] if isinstance(rows, list) else rows['contract'][0]
            self.assertIn('period_metadata_scope', row)

    def test_artifact_payload_is_not_a_ledger_record(self):
        original = {'id': 'a', 'kind': 'artifact', 'owner': 'buyer', 'revision': 1,
                    'content': self.contract(), 'records': [self.contract()]}
        # Content is preserved. Real response envelopes should not be confused
        # with arbitrary fields inside an actual record.
        result = period_scope_view(original)
        self.assertEqual(result, original)

    def test_offer_and_milestone(self):
        offer = {'id': 'o', 'kind': 'offer', 'owner': 'seller', 'revision': 1,
                 'mode': 'milestone', 'period_seconds': 14400}
        self.assertFalse(period_scope_view(offer)['period_metadata_scope']['applies_to_subscription_billing'])

    def test_actual_reads_receipts_and_canonical_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(Path(tmp) / 'world', lambda: 1000)
            w.create('market', hours=24)
            offer = w.act('reach', 'offer', {'op': 'offer', 'title': 'Access',
                'terms': 'Access during this run', 'price': 1, 'delivery': 'GET /'})['result']
            purchase = {'op': 'checkout', 'offer': offer['id'], 'agreed_price': 1}
            original = w.act('northstar', 'purchase', purchase)
            identity = original['result']['id']
            with w.s.transaction() as db:
                canonical = w.s.get(db, identity)
                cfg = w.s.meta(db, 'config')
                w.s.meta(db, 'config', dict(cfg, commerce_period_scope_view=True))
            self.assertEqual(w.act('northstar', 'purchase', purchase), original)
            inspected = w.act('reach', 'read', {'op': 'inspect', 'id': identity})['result']
            self.assertFalse(inspected['period_metadata_scope']['applies_to_subscription_billing'])
            delivered = w.act('reach', 'delivery', {'op': 'deliver', 'contract': identity,
                'reference': 'GET /', 'reason': 'Access route'})['result']
            self.assertIn('period_metadata_scope', delivered)
            view = w.view('reach', 'contract')
            self.assertIn('period_metadata_scope', view['records'][0])
            with w.s.transaction() as db:
                after = w.s.get(db, identity)
                self.assertEqual(after['terms'], canonical['terms'])
                self.assertNotIn('period_metadata_scope', after)
                import json
                event = json.loads(db.execute("SELECT body FROM events WHERE kind='exposure' ORDER BY seq DESC LIMIT 1").fetchone()[0])
                self.assertEqual(event['result_hash'], digest(view))


if __name__ == '__main__':
    unittest.main()

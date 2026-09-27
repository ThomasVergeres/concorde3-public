import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import companies, field_consumers, field_seeds
from experiments.company_market import Market
from experiments.public_research import destination


class FieldTests(unittest.TestCase):
    def tearDown(self):
        companies.set_profile("classic")

    def test_profile_has_five_independent_companies_and_fixed_cap(self):
        companies.set_profile("field")
        self.assertEqual(len(companies.ALL), 5)
        self.assertEqual(3 * 8 + 2 * 10, 44)
        self.assertEqual(companies.capacity("beacon", {}, 100), 6)
        self.assertEqual(companies.capacity("beacon", {"boost": [{"actor": "beacon", "until": 101}]}, 100), 10)
        self.assertEqual(companies.capacity("beacon", {"boost": [{"actor": "beacon", "until": 100}]}, 100), 6)

    def test_research_rejects_private_and_mixed_dns_destinations(self):
        def dns(*_args, **_kw):
            return [(2, 1, 6, "", ("127.0.0.1", 80))]
        for url in ("http://localhost/", "file:///etc/passwd", "https://user:secret@example.com/", "https://example.com:9999/"):
            with self.assertRaises(ValueError):
                destination(url, dns)
        def mixed(*_args, **_kw):
            return [(2, 1, 6, "", (a, 443)) for a in ("1.1.1.1", "169.254.169.254")]
        with self.assertRaises(ValueError):
            destination("https://example.com/", mixed)

    def test_research_dials_public_resolution(self):
        def dns(*_args, **_kw):
            return [(2, 1, 6, "", ("1.1.1.1", 443))]
        p, address, port = destination("https://example.com/a?q=b", dns)
        self.assertEqual((p.hostname, address, port), ("example.com", "1.1.1.1", 443))
        with patch.dict(os.environ, {"RESEARCH_DENY_IPS": "1.1.1.1"}):
            with self.assertRaises(ValueError):
                destination("https://example.com/a?q=b", dns)

    def test_workload_changes_are_idempotent_and_have_real_variation(self):
        self.assertEqual(field_seeds.orders(8), field_seeds.orders(8))
        self.assertTrue(any(o["format"] == "json" for o in field_seeds.orders(8)))
        self.assertTrue(any(not o["paid"] for o in field_seeds.orders(8)))
        self.assertGreater(len(field_seeds.orders(24)), len(field_seeds.orders(8)))

    def test_consumer_does_not_accept_wrong_product_format_or_unpaid_delivery(self):
        inputs = {"orders": [{"id": "one", "sku": "x", "customer": "a", "paid": True, "format": "json"}]}
        result = field_consumers.evaluate("parcel", inputs, lambda _: (200, {"order": "one", "sku": "x", "format": "markdown", "content": "hello"}))
        self.assertFalse(result[0]["fulfilled_requested_format"])
        result = field_consumers.evaluate("parcel", inputs, lambda _: (200, {"order": "one", "sku": "x", "format": "json", "content": "not json"}))
        self.assertFalse(result[0]["fulfilled_requested_format"])
        inputs["orders"][0]["paid"] = False
        self.assertTrue(field_consumers.evaluate("parcel", inputs, lambda _: (200, {"content": "private product"}))[0]["unpaid_delivery"])

    def test_publication_metadata_is_not_claimed_as_truth(self):
        inputs = {"subscribers": [{"id": "reader", "interest": "business"}]}
        result = field_consumers.evaluate("beacon", inputs, lambda _: (200, {"articles": [{"id": "x", "topics": ["business"], "summary": "X" * 90, "source_url": "https://example.com", "source_retrieved_at": 123}]}))
        self.assertEqual(result[0]["sourced_articles"], 1)
        self.assertIn("not verification", result[0]["limitation"])

    def test_supplier_can_see_usage_without_private_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config.json").write_text(json.dumps({"started": 100, "cutoff": 10000, "balances": {"a": 6}, "actors": {"a": {"role": "venture", "token": "a", "description": "a"}}}))
            market = Market(root, lambda: 200)
            with market.connect() as db:
                market.put(db, "use", {"id": "use", "at": 150, "actor": "b", "seller": "a", "method": "POST", "path": "/private?secret=1", "body": "private", "status": "returned", "http_status": 200})
            result = market.read("a", "incoming_uses")
            self.assertEqual(len(result), 1)
            self.assertNotIn("body", result[0])
            self.assertNotIn("path", result[0])
            self.assertEqual(market.read("a", "uses"), [])

    def test_customer_can_buy_capacity_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config.json").write_text(json.dumps({"started": 100, "cutoff": 10000, "allow_customer_boost": True,
                "balances": {"beacon": 48, "capacity": 0}, "actors": {"beacon": {"role": "customer", "boosted_starts": 10, "token": "b", "description": "b"}}}))
            market = Market(root, lambda: 200)
            result = market.act("beacon", "lease", {"op": "boost"})
            self.assertEqual(result["starts_per_hour"], 10)
            self.assertEqual(market.read("beacon", "account")["balance"], 44)


if __name__ == "__main__":
    unittest.main()

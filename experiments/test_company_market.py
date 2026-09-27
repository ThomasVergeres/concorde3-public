import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from experiments import companies
from experiments.company_market import Market, Rejected
from experiments.company_seeds import ALL, VENTURES, CUSTOMERS, world


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 10000.0
        config = {"started": self.now, "cutoff": self.now + 43200,
                  "actors": {name: {"token": name + '-token', "role": "venture" if name in VENTURES else "customer", "description": name, "product_host": "127.0.0.1"} for name in ALL},
                  "balances": {**{n: 6 for n in VENTURES}, **{n: 48 for n in CUSTOMERS}, "treasury": 120, "capacity": 0, "escrow": 0}}
        (self.root / "config.json").write_text(json.dumps(config))
        self.market = Market(self.root, lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def act(self, actor, key, **data):
        return self.market.act(actor, key, data)

    def order(self, key="order", amount=3):
        return self.act("lantern", key, op="order", seller="vector", amount=amount, terms="a useful service")

    def test_initial_currency_and_identity(self):
        with self.market.connect() as db:
            self.assertEqual(db.execute("SELECT sum(balance) FROM accounts").fetchone()[0], 240)
        self.assertEqual(self.market.actor("vector-token"), "vector")
        with self.assertRaises(Rejected):
            self.market.actor("bogus")

    def test_atomic_receipts_collision_and_replay(self):
        first = self.order()
        self.assertEqual(self.order(), first)
        self.assertEqual(self.market.read("lantern", "account")["balance"], 45)
        with self.assertRaises(Rejected):
            self.order(amount=4)
        self.assertEqual(len(self.market.read("lantern", "orders")), 1)

    def test_concurrent_overspend_and_conservation(self):
        def buy(n):
            try:
                return self.order(key=str(n), amount=10)
            except Rejected:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            successes = list(pool.map(buy, range(12)))
        self.assertEqual(sum(x is not None for x in successes), 4)
        with self.market.connect() as db:
            self.assertEqual(db.execute("SELECT sum(balance) FROM accounts").fetchone()[0], 240)

    def test_private_records_and_no_self_purchase(self):
        self.order()
        self.act("lantern", "message", op="message", to="vector", text="private")
        self.assertEqual(self.market.read("harbor", "messages"), [])
        self.assertEqual(self.market.read("harbor", "orders"), [])
        with self.assertRaises(Rejected):
            self.act("vector", "bad", op="order", seller="vector", amount=1, terms="wash")

    def test_accept_requires_delivery_and_real_buyer_use(self):
        order = self.order()
        with self.assertRaises(Rejected):
            self.act("vector", "steal", op="accept", order=order["id"], reason="seller says fine")
        self.act("vector", "delivery", op="deliver", order=order["id"], reason="ready at /")
        with self.assertRaises(Rejected):
            self.act("lantern", "no-use", op="accept", order=order["id"], reason="looks fine", alternative="DIY", use="nonexistent")
        with self.market.connect() as db:
            self.market.put(db, "use", {"id": "fixture-use", "actor": "lantern", "seller": "vector", "at": self.now, "status": "returned", "http_status": 200})
        result = self.act("lantern", "yes", op="accept", order=order["id"], reason="verified result", alternative="DIY", use="fixture-use")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.market.read("vector", "account")["balance"], 9)
        with self.assertRaises(Rejected):
            self.act("lantern", "second", op="accept", order=order["id"], reason="duplicate", alternative="DIY", use="fixture-use")

    def test_rejection_refunds_and_is_idempotent(self):
        order = self.order()
        self.act("lantern", "reject", op="reject", order=order["id"], reason="not useful")
        self.act("lantern", "reject", op="reject", order=order["id"], reason="not useful")
        self.assertEqual(self.market.read("lantern", "account")["balance"], 48)
        self.assertEqual(len(self.market.read("vector", "reviews")), 1)

    def test_capacity_lease_and_expiry(self):
        boost = self.act("vector", "boost", op="boost")
        self.assertEqual(self.market.read("vector", "account")["balance"], 2)
        self.assertEqual(companies.capacity("vector", {"boost": [boost]}, self.now), 8)
        self.assertEqual(companies.capacity("vector", {"boost": [boost]}, self.now + 3600), 4)
        with self.assertRaises(Rejected):
            self.act("vector", "again", op="boost")
        with self.assertRaises(Rejected):
            self.act("lantern", "boost", op="boost")
        self.now += 42000
        with self.assertRaises(Rejected):
            self.act("mosaic", "too-late", op="boost")

    def test_injection_is_treasury_transfer_not_minting(self):
        self.now += 7200
        self.assertEqual(self.market.read("lantern", "account")["balance"], 52)
        self.assertEqual(self.market.read("lantern", "account")["balance"], 52)
        with self.market.connect() as db:
            self.assertEqual(db.execute("SELECT sum(balance) FROM accounts").fetchone()[0], 240)
            self.assertEqual(db.execute("SELECT balance FROM accounts WHERE id='treasury'").fetchone()[0], 112)

    def test_freeze_blocks_new_effects_but_reconciles_old(self):
        order = self.order()
        self.now += 43201
        self.assertEqual(self.order(), order)
        with self.assertRaises(Rejected):
            self.order("new")

    def test_reserved_product_request_not_replayed(self):
        with patch.object(self.market, "dispatch", side_effect=RuntimeError("crash before response")) as dispatch:
            with self.assertRaises(RuntimeError):
                self.act("lantern", "use", op="use", seller="vector", path="/")
            result = self.act("lantern", "use", op="use", seller="vector", path="/")
            self.assertEqual(result["status"], "reserved")
            self.assertEqual(dispatch.call_count, 1)

    def test_product_proxy_does_not_accept_arbitrary_targets(self):
        for path in ("http://169.254.169.254/", "//127.0.0.1/", "/x\r\nHeader:value"):
            with self.assertRaises(Rejected):
                self.act("lantern", path, op="use", seller="vector", path=path)
        with self.assertRaises(Rejected):
            self.act("lantern", "host", op="use", seller="host", path="/")

    def test_cohort_resource_math_and_broad_seeds(self):
        self.assertEqual(len(ALL), 6)
        self.assertEqual(len(VENTURES) * 8 + len(CUSTOMERS) * 6, 44)
        for name, (_, goal) in ALL.items():
            self.assertNotIn("artifacts/", goal)
            self.assertNotIn("pass", goal)
            self.assertIn("README.md", world(name))

    def test_deadline_float_serialization_is_not_a_mutation(self):
        self.assertTrue(companies.same_deadline("1970-01-01T02:46:40.123456Z", 10000.1234562))
        self.assertFalse(companies.same_deadline("1970-01-01T02:46:41Z", 10000))

    def test_process_health_keeps_docker_required_pid_column(self):
        with patch.object(companies, "run", return_value="PID COMMAND\n123 concorde3 run /instance") as command:
            self.assertTrue(companies.process_present("subject", "concorde3 run /instance"))
            command.assert_called_once_with("docker", "top", "subject", "-eo", "pid,args")


if __name__ == "__main__":
    unittest.main()

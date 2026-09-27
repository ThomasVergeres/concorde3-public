import tempfile
from pathlib import Path
import unittest

from worlds.engine import World


class ServiceLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 1000.
        self.world = World(Path(self.tmp.name) / "world", lambda: self.now)
        self.world.create("consumer", hours=1)

    def test_provider_boundary_is_public_not_a_private_seller_schedule(self):
        for actor in ("everyday", "theo"):
            for section in ("overview", "help"):
                view = self.world.view(actor, section)["world"]
                self.assertEqual(view["cutoff"], 4600.)
                service = view["action_service"]
                self.assertTrue(service["accepting_actions"])
                self.assertIn("individual", service["cutoff_scope"])
                self.assertNotIn("intention", str(service))

    def test_current_unavailability_is_distinct_from_advertised_cutoff(self):
        self.world.freeze()
        view = self.world.view("theo", "help")["world"]
        self.assertEqual(view["cutoff"], 4600.)
        self.assertFalse(view["action_service"]["accepting_actions"])

    def test_elapsed_boundary_is_not_advertised_as_live(self):
        self.now = 4600.
        self.assertFalse(self.world.view("theo", "help")["world"]["action_service"]["accepting_actions"])

    def test_refund_contract_names_existing_requirements_not_a_reserve_policy(self):
        terms = self.world.view("theo", "help")["world"]["checkout_refunds"]
        self.assertFalse(terms["seller_approval_required_within_agreed_window"])
        self.assertTrue(terms["requires_seller_funds"])
        self.assertTrue(terms["requires_available_provider"])
        self.assertIn("purchased", terms["window_basis"])
        self.assertNotIn("reserve", str(terms))


if __name__ == "__main__": unittest.main()

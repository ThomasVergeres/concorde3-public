"""Hosted scheduling is not an external mailbox capability. No model calls."""
import json
from pathlib import Path
import tempfile
import unittest

from worlds.engine import World
from worlds.store import Rejected
from worlds.campaign import counterpart_due
from worlds.test_world import Clock


class ContinuationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.world = World(Path(self.tmp.name) / "world", self.clock)
        self.world.create("consumer", hours=2)

    def test_external_client_does_not_advertise_hosted_scheduling(self):
        help = self.world.view("everyday", "help")
        overview = self.world.view("everyday")
        self.assertNotIn("defer", help["operations"])
        self.assertNotIn("defer", help["schema"])
        self.assertNotIn("defer", help["schemas"])
        self.assertNotIn("continuation", overview)
        for view in (help, overview):
            mailbox = view["world"]["mailbox"]
            self.assertEqual(mailbox["delivery"], "pull")
            self.assertFalse(mailbox["client_execution_triggered"])
            for private_term in ("concorde", "node", "intention", "graph"):
                self.assertNotIn(private_term, json.dumps(mailbox).lower())

    def test_external_defer_rejected_without_mutation_or_receipt(self):
        with self.world.s.transaction() as db:
            before = self.world.s.get(db, "continuation:everyday")
            events = db.execute("SELECT count(*) FROM events").fetchone()[0]
            receipts = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        with self.assertRaisesRegex(Rejected, "does not schedule external client execution"):
            self.world.act("everyday", "unsupported", {"op": "defer", "after_seconds": 60, "reason": "Return later"})
        with self.world.s.transaction() as db:
            self.assertEqual(self.world.s.get(db, "continuation:everyday"), before)
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], events)
            self.assertEqual(db.execute("SELECT count(*) FROM receipts").fetchone()[0], receipts)

    def test_hosted_defer_and_unread_response_still_work(self):
        help = self.world.view("maya", "help")
        self.assertIn("defer", help["operations"])
        self.assertIn("defer", help["schema"])
        self.assertIn("defer", help["schemas"])
        result = self.world.act("maya", "defer", {"op": "defer", "after_seconds": 600, "reason": "Read later"})
        self.assertEqual(result["result"]["next_at"], self.clock.now + 600)
        self.world.act("everyday", "message", {"op": "message", "to": "maya", "text": "Requested result is ready"})
        overview = self.world.view("maya")
        self.assertTrue(overview["records"]["message"])
        self.assertFalse(counterpart_due(overview["continuation"], False, self.clock.now))
        self.assertTrue(counterpart_due(overview["continuation"], True, self.clock.now))

    def test_external_messages_remain_readable_without_changed_schedule(self):
        with self.world.s.transaction() as db:
            before = self.world.s.get(db, "continuation:everyday")
        sent = self.world.act("maya", "reply", {"op": "message", "to": "everyday", "text": "A customer reply"})
        for section in ("overview", "message"):
            view = self.world.view("everyday", section)
            messages = view["records"]["message"] if section == "overview" else view["records"]
            self.assertIn(sent["result"]["id"], [m["id"] for m in messages])
        with self.world.s.transaction() as db:
            self.assertEqual(self.world.s.get(db, "continuation:everyday"), before)
        restarted = World(self.world.s.root, self.clock)
        self.assertNotIn("continuation", restarted.view("everyday"))
        self.assertEqual(restarted.s.verify()["events"], self.world.s.verify()["events"])


if __name__ == "__main__":
    unittest.main()

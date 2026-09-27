import json
from pathlib import Path
import tempfile
import unittest

from experiments.actor_context_shadow import augment_prompt
from worlds.engine import World
from worlds.store import encoded
from worlds.test_world import Clock


class ActorContextShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clock = Clock(); self.world = World(Path(self.tmp.name)/"world", self.clock)
        self.world.create()
        self.artifact = self.world.act("northstar", "own", {"op": "artifact", "title": "Working copy", "content": {"records": []}})["result"]
        self.packet = {"context": {"view": {"world": {"now": self.clock.now}, "records": {
            "observation": [{"id": "observed", "artifact": self.artifact["id"]}],
            "project": [{"artifact": self.artifact["id"], "operation": {"latest": {"artifact": self.artifact["id"]}}}]}}},
            "earlier_results": ["existing receipts"], "turn": 3, "maximum_messages": 4}
        self.database = self.world.s.root/"world.sqlite"

    def prompt(self):
        return "Original instructions unchanged\n"+encoded(self.packet)

    def test_only_available_identity_is_added_and_database_unchanged(self):
        before = self.world.s.verify()
        revised = augment_prompt(self.prompt(), self.database, "northstar")
        prefix, raw = revised.rsplit("\n", 1); result = json.loads(raw)
        self.assertEqual(prefix, "Original instructions unchanged")
        records = result["context"]["view"]["records"]
        identity = records["observation"][0].pop("evaluated_artifact")
        self.assertEqual(identity["owner"], "northstar")
        self.assertEqual(records["project"][0].pop("selected_artifact"), identity)
        self.assertEqual(records["project"][0]["operation"]["latest"].pop("evaluated_artifact"), identity)
        self.assertEqual(result, self.packet)
        self.assertEqual(self.world.s.verify(), before)

    def test_private_source_identity_is_not_leaked(self):
        with self.assertRaisesRegex(ValueError, "not visible"):
            augment_prompt(self.prompt(), self.database, "reach")

    def test_later_artifact_cannot_be_backfilled_into_earlier_prompt(self):
        self.packet["context"]["view"]["world"]["now"] -= 1
        with self.assertRaisesRegex(ValueError, "later/revised"):
            augment_prompt(self.prompt(), self.database, "northstar")

    def test_revision_cannot_be_silently_treated_as_original_content(self):
        with self.world.s.transaction() as db:
            self.world.s.revise(db, self.world.s.get(db, self.artifact["id"]), title="Changed")
        with self.assertRaisesRegex(ValueError, "later/revised"):
            augment_prompt(self.prompt(), self.database, "northstar")

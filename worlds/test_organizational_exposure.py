import json
from pathlib import Path
import tempfile
import unittest

from worlds.engine import World
from worlds.organizational_exposure import schedule, tick
from worlds.scenarios import receiving_contract
from worlds.test_world import Clock


class OrganizationalExposureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()

    def world(self, name, pack="market"):
        world = World(Path(self.tmp.name) / name, self.clock)
        world.create(pack, seed=137, hours=2)
        return world

    def test_source_and_request_use_ordinary_private_receipts(self):
        world = self.world("w")
        result = tick(world, "steward", 0)
        self.assertEqual(len(result["delivered"]), 1)
        with world.s.transaction() as db:
            source = world.s.get(db, result["delivered"][0]["source_artifact"], "steward")
            message = world.s.get(db, result["delivered"][0]["message"], "steward")
            self.assertEqual(source["owner"], "northstar")
            self.assertEqual(source["content"]["task"], world.s.get(db, "project:northstar")["task"])
            self.assertEqual(message["to"], "steward")
            self.assertIn(source["id"], message["text"])
            self.assertEqual(world.s.rows(db, "contract"), [])
            self.assertEqual(world.s.rows(db, "observation"), [])
            self.assertEqual(world.s.rows(db, "artifact", "frontier"), [])

    def test_repeated_tick_is_idempotent_even_when_project_changes(self):
        world = self.world("w")
        tick(world, "steward", 0)
        before = world.s.verify()
        self.assertEqual(tick(world, "steward", 0)["delivered"], [])
        self.assertEqual(world.s.verify(), before)

    def test_source_discloses_optional_machine_receiving_interface(self):
        for pack, subject in (("market", "steward"), ("consumer", "everyday")):
            with self.subTest(pack=pack):
                world = self.world(pack, pack)
                delivery = tick(world, subject, 0)["delivered"][0]
                with world.s.transaction() as db:
                    payload = world.s.get(db, delivery["source_artifact"], subject)["content"]
                    self.assertEqual(payload.get("receiving_contract"), receiving_contract(payload["task"]))
                    self.assertIn("optional", payload.get("integration_note", "").lower())
                    self.assertNotIn("solution", payload)
                    self.assertNotIn("private_memory", payload)

    def test_later_source_receiving_interface_tracks_current_input(self):
        world = self.world("consumer", "consumer")
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg["period_seconds"] = 1200
            world.s.meta(db, "config", cfg)
        first = tick(world, "everyday", 0)["delivered"][0]
        self.clock.now += 22 * 60
        world.tick()
        second = tick(world, "everyday", 22 * 60)["delivered"][0]
        with world.s.transaction() as db:
            a = world.s.get(db, first["source_artifact"])["content"]
            b = world.s.get(db, second["source_artifact"])["content"]
            self.assertNotEqual(a["task"]["catalog"], b["task"]["catalog"])
            self.assertEqual(b.get("receiving_contract"), receiving_contract(b["task"]))

    def test_matching_seed_arms_share_payload_not_generated_ids(self):
        a, b = self.world("a"), self.world("b")
        ra, rb = tick(a, "steward", 0), tick(b, "steward", 0)
        def payload(world, result):
            with world.s.transaction() as db:
                return world.s.get(db, result["delivered"][0]["source_artifact"])["content"]
        self.assertEqual(payload(a, ra), payload(b, rb))

    def test_schedule_exceptions_and_stop_are_not_rubric_coaching(self):
        world = self.world("w", "consumer")
        origin = self.clock.now
        all_deliveries = []
        for row in schedule():
            self.clock.now = origin + row["offset_seconds"]
            all_deliveries.extend(tick(world, "everyday", row["offset_seconds"])["delivered"])
        self.assertEqual(len(all_deliveries), 7)
        with world.s.transaction() as db:
            messages = world.s.rows(db, "message", "everyday")
            text = " ".join(m["text"] for m in messages).lower()
            for forbidden in ("please automate", "systematiz", "systemiz", "activation", "graph", "benchmark", "rubric", "candidate", "control arm"):
                self.assertNotIn(forbidden, text)
            self.assertIn("stop proactive", text)
            self.assertIn("no payment", text)
            self.assertEqual(world.s.rows(db, "contract"), [])

    def test_no_future_or_post_cutoff_delivery_and_bad_pair_rejected(self):
        world = self.world("w")
        self.assertEqual(len(tick(world, "steward", 94 * 60)["delivered"]), 1)
        with self.assertRaises(ValueError):
            tick(self.world("other"), "everyday", 0)
        self.clock.now += 7200
        self.assertTrue(tick(world, "steward", 7200)["stopped"])

    def test_delayed_exposure_is_not_delivered_as_fresh_opportunity(self):
        world = self.world("w")
        self.clock.now += 25 * 60
        result = tick(world, "steward", 25 * 60)
        self.assertEqual(len(result["delivered"]), 1)  # minute-22 repeat, not stale opening
        with world.s.transaction() as db:
            self.assertEqual(world.s.meta(db, "organizational-exposure-v1:steward:0")["status"], "missed")

    def test_repeat_follows_real_period_change(self):
        world = self.world("w")
        with world.s.transaction() as db:
            cfg = world.s.meta(db, "config")
            cfg["period_seconds"] = 1200
            world.s.meta(db, "config", cfg)
        first = tick(world, "steward", 0)["delivered"][0]
        self.clock.now += 22 * 60
        world.tick()
        second = tick(world, "steward", 22 * 60)["delivered"][0]
        with world.s.transaction() as db:
            a = world.s.get(db, first["source_artifact"])["content"]["task"]
            b = world.s.get(db, second["source_artifact"])["content"]["task"]
            self.assertNotEqual(a, b)

    def test_interrupted_delivery_reuses_original_source_payload(self):
        world = self.world("w")
        original = world.act
        def interrupted(actor, key, action):
            if action["op"] == "message":
                raise RuntimeError("interruption after source receipt")
            return original(actor, key, action)
        world.act = interrupted
        with self.assertRaises(RuntimeError):
            tick(world, "steward", 0)
        with world.s.transaction() as db:
            project = world.s.get(db, "project:northstar")
            world.s.revise(db, project, task={"adapter": "records", "records": []})
        world.act = original
        result = tick(world, "steward", 0)
        self.assertEqual(len(result["delivered"]), 1)
        with world.s.transaction() as db:
            self.assertEqual(len(world.s.rows(db, "artifact")), 1)
            self.assertEqual(len(world.s.rows(db, "message")), 1)
            source = world.s.get(db, result["delivered"][0]["source_artifact"])
            self.assertTrue(source["content"]["task"]["records"])


if __name__ == "__main__":
    unittest.main()

import copy
from pathlib import Path
import tempfile
import unittest

from worlds.engine import World
from worlds.organizational_review import assess, review
from worlds.test_world import Clock


class OrganizationalReviewTests(unittest.TestCase):
    def setUp(self):
        self.records = [{"id": "product", "kind": "artifact", "owner": "seller"}]
        self.state = {"activations": {"a": {"id": "a", "started": 10, "finished": 30,
                                            "intention": "purpose", "status": "completed"}}}

    def observation(self, identity, at=25, task_hash="task-a", work_id="work-a", status="passed", **extra):
        return {"id": identity, "kind": "observation", "owner": "buyer", "artifact": "product",
                "at": at, "task_hash": task_hash, "work_id": work_id, "project": "project:buyer",
                "outcome": {"status": status}, "output": {"value": 1}, **extra}

    def result(self, records=None, events=(), state=None):
        return assess(records or self.records, events, self.state if state is None else state, "seller", 1, 100)

    def test_retries_are_not_new_successes_but_failures_remain(self):
        result = self.result(self.records + [self.observation("o1"), self.observation("o2"),
                                          self.observation("o3", status="failed")])
        self.assertEqual(result["receiving_summary"]["distinct_successful_work"], 1)
        self.assertEqual(result["receiving_summary"]["attempt_outcomes"], {"passed": 2, "failed": 1})

    def test_same_work_label_different_actual_input_is_distinct(self):
        result = self.result(self.records + [self.observation("o1"), self.observation("o2", task_hash="task-b")])
        self.assertEqual(result["receiving_summary"]["distinct_successful_work"], 2)
        self.assertEqual(result["receiving_summary"]["reused_immutable_artifacts"][0]["successful_task_payloads"], 2)
        self.assertIn("unassessed", result["judgment"])

    def test_other_producer_and_incumbent_not_seller_success(self):
        records = self.records + [{"id": "other", "kind": "artifact", "owner": "buyer"},
                                  self.observation("o1", artifact="other"), self.observation("o2", artifact=None)]
        self.assertEqual(self.result(records)["receiving"], [])

    def test_consumption_while_idle_is_not_unattended_production(self):
        result = self.result(self.records + [self.observation("o1"), self.observation("o2", at=50)])
        self.assertEqual([r["activation_overlap"] for r in result["receiving"]],
                         ["overlap", "outside_recorded_activations"])
        self.assertTrue(any("not proof of unattended seller production" in x for x in result["limitations"]))

    def test_unfinished_and_missing_state_are_unknown_not_idle(self):
        unfinished = copy.deepcopy(self.state)
        unfinished["activations"]["a"].pop("finished")
        records = self.records + [self.observation("o1", at=90)]
        self.assertEqual(self.result(records, state=unfinished)["receiving"][0]["activation_overlap"], "unknown")
        self.assertEqual(assess(records, [], None, "seller", 1, 100)["receiving"][0]["activation_overlap"], "unknown")

    def test_delivery_and_served_exposure_are_separate_all_interface_paths(self):
        records = self.records + [{"id": f"m{i}", "kind": "message", "owner": "buyer", "to": "seller", "at": 5}
                                  for i in range(4)]
        events = [{"seq": 1, "at": 12, "actor": "seller", "kind": "exposure", "body": {"record_ids": ["m0"]}},
                  {"seq": 2, "at": 13, "actor": "seller", "kind": "inspect", "body": {"result": {"id": "m1"}}},
                  {"seq": 3, "at": 14, "actor": "seller", "kind": "browse", "body": {"result": {"records": [{"id": "m2"}]}}},
                  {"seq": 4, "at": 15, "actor": "buyer", "kind": "exposure", "body": {"record_ids": ["m3"]}}]
        messages = self.result(records, events)["inbound_messages"]
        self.assertEqual([m["served"]["via"] if m["served"] else None for m in messages], ["exposure", "inspect", "browse", None])

    def test_programs_and_edges_are_inventory_not_competence(self):
        state = {**self.state, "programs": {"p": {"id": "p", "status": "succeeded", "command": ["secret"]}},
                 "edges": {"edge": {"source": "a", "target": "b"}}}
        result = self.result(state=state)
        self.assertEqual(result["graph"]["edge_refs"], ["edge"])
        self.assertNotIn("secret", str(result))
        self.assertEqual(result["judgment"].split(":")[0], "unassessed")

    def test_readonly_review_does_not_create_events_or_expose_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            world = World(Path(temp) / "world", Clock())
            world.create("market")
            before = world.s.verify()
            result = review(world.s.root, "steward", until=1700000100)
            self.assertEqual(world.s.verify(), before)
            self.assertFalse(result["state_available"])
            self.assertNotIn("token", result["provenance"])
            with self.assertRaises(ValueError):
                review(world.s.root, "unknown", until=1700000100)

    def test_declared_derivative_is_visible_without_inflating_direct_success(self):
        with tempfile.TemporaryDirectory() as temp:
            clock = Clock()
            world = World(Path(temp) / "world", clock)
            world.create("market")
            source = world.act("frontier", "source", {
                "op": "message", "to": "relay", "text": "A suggestion to adapt, not an executed product."
            })["result"]
            adapted = world.act("relay", "adapt", {
                "op": "artifact", "title": "Buyer adaptation", "content": {}, "source_refs": [source["id"]]
            })["result"]
            clock.now += 10
            world.act("relay", "try", {"op": "consume", "project": "project:relay", "artifact": adapted["id"]})
            before = world.s.verify()
            result = review(world.s.root, "frontier", until=clock.now)
            self.assertEqual(world.s.verify(), before)
            self.assertEqual(result["receiving_summary"]["distinct_successful_work"], 0)
            lineage = result["source_lineage"]
            self.assertEqual(lineage["matched_observations"], 1)
            self.assertIn("Not proof of causal contribution", lineage["basis"])
            use = lineage["observations"][0]
            self.assertEqual(use["source_paths"], [[adapted["id"], source["id"]]])
            self.assertEqual(use["outcome"]["status"], "failed")
            self.assertFalse(use["observation_visible_to_subject"])

    def test_historical_review_lineage_excludes_later_receiving(self):
        with tempfile.TemporaryDirectory() as temp:
            clock = Clock()
            world = World(Path(temp) / "world", clock)
            world.create("market")
            source = world.act("frontier", "source", {
                "op": "message", "to": "relay", "text": "A contribution."
            })["result"]
            adapted = world.act("relay", "adapt", {
                "op": "artifact", "title": "Adaptation", "content": {}, "source_refs": [source["id"]]
            })["result"]
            clock.now += 10
            world.act("relay", "try1", {"op": "consume", "project": "project:relay", "artifact": adapted["id"]})
            historical_cutoff = clock.now
            clock.now += 10
            world.act("relay", "try2", {"op": "consume", "project": "project:relay", "artifact": adapted["id"]})
            earlier = review(world.s.root, "frontier", until=historical_cutoff)
            current = review(world.s.root, "frontier", until=clock.now)
            self.assertEqual(earlier["source_lineage"]["matched_observations"], 1)
            self.assertEqual(current["source_lineage"]["matched_observations"], 2)


if __name__ == "__main__":
    unittest.main()

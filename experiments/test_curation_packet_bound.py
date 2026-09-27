import copy
import unittest
from experiments import discrepancy_engine as engine
from experiments.discrepancy_store import encoded


class PacketBoundTests(unittest.TestCase):
    def test_large_case_remains_inspectable_with_every_source_and_honest_excerpts(self):
        packet = {"sources": {"catalog": {"text": "contract", "truncated": False},
            "a": {"text": "α"*40000, "blob": "blobs/abc", "truncated": False},
            "b": {"text": "b"*40000, "truncated": False}}}
        before = copy.deepcopy(packet)
        result = engine.bound_curation_packet(packet, limit=12000, protected=("catalog",))
        self.assertLessEqual(len(encoded(result).encode()), 12000)
        self.assertEqual(set(result["sources"]), set(packet["sources"]))
        self.assertEqual(result["sources"]["catalog"], packet["sources"]["catalog"])
        self.assertTrue(result["sources"]["a"]["truncated"])
        self.assertEqual(result["sources"]["a"]["blob"], "blobs/abc")
        self.assertEqual(result["sources"]["a"]["full_text_sha256"], engine.sha256_bytes(before["sources"]["a"]["text"].encode()))
        self.assertEqual(packet, before)

    def test_cannot_shrink_required_contract_or_drop_sources_to_force_fit(self):
        with self.assertRaises(ValueError):
            engine.bound_curation_packet({"sources": {"contract": {"text": "x"*20000}}}, limit=3000, protected=("contract",))

"""World qualification: delivered products are incoming changes without a chat ping."""
import json
import tempfile
from pathlib import Path
import unittest

from worlds.engine import World
from worlds.driver import episode


class DeliveryAttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.world=World(Path(self.tmp.name)/"world"); self.world.create("market")
        w=self.world
        offer=w.act("reach","offer",{"op":"offer","title":"Prepared result","price":1,"terms":"One result","delivery":"report"})["result"]
        self.contract=w.act("northstar","buy",{"op":"checkout","offer":offer["id"],"agreed_price":1})["result"]["id"]
        with w.s.transaction() as db:
            cfg=w.s.meta(db,"config"); cfg.update(counterpart_calls_per_hour=4,counterpart_response_reserve=2); w.s.meta(db,"config",cfg)
        for _ in range(2):
            call=w.reserve_call("northstar"); w.finish_call(call["id"],"completed",{"synthetic_witness":True})

    def deliver(self,key):
        return self.world.act("reach",key,{"op":"deliver","contract":self.contract,"reference":"report-"+key,"reason":"Ready to inspect"})

    def capacity(self):
        with self.world.s.transaction() as db: return self.world.counterpart_capacity(db,"northstar")

    def test_delivery_alone_releases_response_headroom(self):
        self.assertFalse(self.capacity()["admissible"])
        self.deliver("first")
        self.assertTrue(self.capacity()["admissible"])
        self.assertTrue(self.capacity()["responding"])

    def test_only_served_delivery_is_consumed_and_new_one_remains(self):
        self.deliver("first")
        def model(actor,prompt):
            self.assertIn("report-first",prompt)
            call=self.world.reserve_call(actor);self.world.finish_call(call["id"],"completed",{"synthetic_witness":True})
            self.deliver("second")
            return {"actions":[],"finish":True,"note":"Read first delivery"}
        episode(self.world,"northstar",model)
        self.assertTrue(self.capacity()["responding"])
        def next_model(actor,prompt):
            self.assertIn("report-second",prompt)
            return {"actions":[],"finish":True,"note":"Read second delivery"}
        episode(self.world,"northstar",next_model)
        self.assertFalse(self.capacity()["responding"])
        self.assertFalse(self.capacity()["admissible"])
        self.deliver("second")  # exact replay does not manufacture new demand
        self.assertFalse(self.capacity()["responding"])

import tempfile
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import unittest
from worlds.engine import World
from worlds.test_world import Clock
from worlds import lived_probe, scenarios


class LivedProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.clock=Clock();self.w=World(Path(self.tmp.name)/"world",self.clock);self.w.create("market")

    def test_normal_mail_and_distinct_batches_not_private_wakes(self):
        first=lived_probe.deliver(self.w,0,"steward",True,91)
        second=lived_probe.deliver(self.w,1,"steward",True,91)
        self.assertNotEqual(first["task"]["records"],second["task"]["records"])
        self.assertIn(first["message"],[m["id"] for m in self.w.view("steward","message")["records"]])
        self.assertIn("one-off",lived_probe.brief(first["task"],False))

    def test_valid_delivery_bad_delivery_and_no_duplicate_credit(self):
        entry=lived_probe.deliver(self.w,0,"steward",True,91)
        self.w.act("steward","wrong",{"op":"artifact","title":"Import","content":{**scenarios.baseline(entry["task"]),"total_amount":-1},"audience":["ledgerbird"]})
        self.w.act("steward","right",{"op":"artifact","title":"Import","content":scenarios.baseline(entry["task"]),"audience":["ledgerbird"]})
        seen=set();attempts=lived_probe.receive(self.w,entry,"steward",seen)
        self.assertEqual([a["outcome"]["status"] for a in attempts],["failed","passed"])
        self.assertEqual(lived_probe.receive(self.w,entry,"steward",seen),[])
        altered={**entry,"task":lived_probe.make_task(2,91)}
        self.assertEqual(lived_probe.receive(self.w,altered,"steward",set()),[])

    def test_private_and_other_supplier_artifacts_not_consumed(self):
        entry=lived_probe.deliver(self.w,0,"steward",True,91)
        for owner,audience in (("steward",[]),("frontier",["ledgerbird"])):
            self.w.act(owner,"output",{"op":"artifact","title":"Import","content":scenarios.baseline(entry["task"]),"audience":audience})
        self.assertEqual(lived_probe.receive(self.w,entry,"steward",set()),[])

    def test_existing_service_handles_distinct_later_batches_once_each(self):
        class Product(BaseHTTPRequestHandler):
            calls=[]
            def log_message(self,*_):pass
            def do_POST(self):
                task=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                Product.calls.append(task)
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps(scenarios.baseline(task)).encode())
        service=ThreadingHTTPServer(("127.0.0.1",0),Product)
        thread=threading.Thread(target=service.serve_forever);thread.start()
        self.addCleanup(thread.join);self.addCleanup(service.server_close);self.addCleanup(service.shutdown)
        with self.w.s.transaction() as db:
            self.w.s.meta(db,"endpoints",{"steward":{"host":"127.0.0.1","port":service.server_port}})
        self.w.act("steward","service",{"op":"artifact","title":"Importer","content":{"endpoint":{"path":"/import","method":"POST"}},"audience":["ledgerbird"]})
        for index in range(2):
            self.clock.now+=10
            entry=lived_probe.deliver(self.w,index,"steward",True,91)
            seen=set();attempts=lived_probe.receive(self.w,entry,"steward",seen)
            self.assertEqual(len(attempts),1)
            self.assertEqual(attempts[0]["outcome"]["status"],"passed")
            self.assertTrue(attempts[0]["receipt"])
            self.assertEqual(lived_probe.receive(self.w,entry,"steward",seen),[])
        self.assertEqual(len(Product.calls),2)
        self.assertNotEqual(Product.calls[0]["records"],Product.calls[1]["records"])

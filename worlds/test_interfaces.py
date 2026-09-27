import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worlds.engine import World
from worlds.server import server
from worlds.store import Rejected
from worlds import runtime, scenarios
from worlds.observe import review_packet, report
from worlds.test_world import Clock


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.w = World(Path(self.tmp.name)/"world", self.clock)
        self.w.create()

    def test_real_http_authorization_and_payment(self):
        service = server(self.w)
        thread = threading.Thread(target=service.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(service.server_close)
        self.addCleanup(service.shutdown)
        with self.w.s.transaction() as db:
            token = db.execute("SELECT token FROM actors WHERE id='reach'").fetchone()[0]
        base = "http://127.0.0.1:"+str(service.server_port)
        with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(base+"/v1/overview")
        req = urllib.request.Request(base+"/v1/actions", data=json.dumps({"op": "publish", "title": "Our product", "text": "Public evidence"}).encode(),
            headers={"Authorization": "Bearer "+token, "Idempotency-Key": "first"})
        result = json.load(urllib.request.urlopen(req))
        self.assertEqual(result, json.load(urllib.request.urlopen(req)))

    def test_real_product_receipt_and_no_repeat_side_effect(self):
        class Product(BaseHTTPRequestHandler):
            calls = 0
            def log_message(self, *_): pass
            def do_POST(self):
                Product.calls += 1
                raw = b'{"hello":"world"}'
                self.send_response(200); self.end_headers(); self.wfile.write(raw)
        product = ThreadingHTTPServer(("127.0.0.1", 0), Product)
        thread = threading.Thread(target=product.serve_forever)
        thread.start()
        self.addCleanup(thread.join); self.addCleanup(product.server_close); self.addCleanup(product.shutdown)
        with self.w.s.transaction() as db:
            self.w.s.meta(db, "endpoints", {"reach": {"host": "127.0.0.1", "port": product.server_port}})
        data = {"op": "use", "seller": "reach", "method": "POST", "path": "/generate"}
        first = self.w.act("northstar", "http-once", data)
        second = self.w.act("northstar", "http-once", data)
        self.assertEqual(first, second)
        self.assertEqual(Product.calls, 1)
        self.assertEqual(first["result"]["status"], "returned")
        self.assertEqual(self.w.act("northstar", "inspect", {"op": "inspect", "id": first["result"]["artifact"]})["result"]["content"], {"hello": "world"})

    def test_product_url_cannot_escape_operator_endpoint(self):
        with self.w.s.transaction() as db:
            self.w.s.meta(db, "endpoints", {"reach": {"host": "127.0.0.1", "port": 1}})
        for path in ("http://localhost/", "//localhost/", "/x\r\nHeader:x"):
            with self.assertRaises(Rejected): self.w.act("northstar", path, {"op": "use", "seller": "reach", "path": path})

    def test_ambiguous_product_is_not_retried(self):
        with self.w.s.transaction() as db:
            self.w.s.meta(db, "endpoints", {"reach": {"host": "127.0.0.1", "port": 1}})
        data = {"op": "use", "seller": "reach", "path": "/"}
        first = self.w.act("northstar", "uncertain", data)
        self.assertEqual(first["result"]["status"], "uncertain")
        self.assertEqual(first, self.w.act("northstar", "uncertain", data))

    def test_actual_voucher_admission_and_rectification_once(self):
        for i in range(6):
            c = runtime.reserve(self.w, "reach", str(i), "work")
            runtime.finish(self.w, "reach", c["id"], 0)
        with self.assertRaises(Rejected): runtime.reserve(self.w, "reach", "extra", "work")
        self.w.act("reach", "buy-voucher", {"op": "voucher", "count": 1})
        c = runtime.reserve(self.w, "reach", "extra", "work")
        self.assertTrue(c["charged_admission"])
        runtime.finish(self.w, "reach", c["id"], 0)
        r = runtime.reserve(self.w, "reach", "extra", "rectification")
        runtime.finish(self.w, "reach", r["id"], 0)
        account = self.w.view("reach", "account")
        self.assertEqual(account["balance"], 11)
        self.assertEqual(account["voucher_reservations"], 0)
        with self.assertRaises(Rejected): runtime.reserve(self.w, "reach", "extra2", "work")

    def test_buyer_cannot_claim_subject_budget(self):
        with self.assertRaises(Rejected): runtime.reserve(self.w, "northstar", "pretend", "work")

    def test_blind_review_uses_historical_brief_not_current_project(self):
        self.w.tick()
        observation = self.w.view("common", "observation")["records"][0]
        packet = review_packet(self.w, observation["id"])
        with self.w.s.transaction() as db:
            p = self.w.s.get(db, "project:common")
            self.w.s.revise(db, p, task={"adapter": "novel"})
        self.assertEqual(review_packet(self.w, observation["id"]), packet)
        self.assertNotIn("seller", packet)

    def test_exogenous_world_not_driven_by_sales(self):
        expected = [scenarios.circumstance(10, a, 3) for a in ("northstar", "relay", "kite")]
        self.w.act("reach", "post", {"op": "publish", "title": "Buy", "text": "Buy buy buy"})
        self.assertEqual(expected, [scenarios.circumstance(10, a, 3) for a in ("northstar", "relay", "kite")])

    def test_same_work_cannot_be_counted_repeatedly(self):
        for i in range(3):
            self.w.act("northstar", str(i), {"op": "consume", "project": "project:northstar", "method": "incumbent"})
        p = self.w.view("northstar", "project")["records"][0]
        self.assertEqual(p["work_done"], 1)
        self.assertEqual(report(self.w)["actors"]["northstar"]["distinct_completed_work"], 1)

    def test_freeze_prevents_reserved_call_from_starting(self):
        c = self.w.reserve_call("northstar")
        self.w.freeze()
        with self.assertRaises(Rejected): self.w.finish_call(c["id"], "running", {"container": "not-started"})

    def test_custom_noncommercial_pack_without_engine_changes(self):
        name = "test-inventory"
        scenarios.register_adapter(name, lambda seed, period: {"adapter": name, "required": ["water", "tools"]},
            lambda task: {"present": task["required"]},
            lambda task, output: {"status": "passed" if output.get("present") == task["required"] else "failed"})
        scenarios.register_pack(name, lambda: {"keeper": {"name": "Keeper", "role": "counterpart", "purpose": "Maintain a useful shared inventory", "adapter": name}})
        self.addCleanup(lambda: scenarios.PACKS.pop(name))
        self.addCleanup(lambda: scenarios.ADAPTERS.pop(name))
        w = World(Path(self.tmp.name)/"custom", self.clock)
        w.create(name)
        w.tick()
        self.assertEqual(w.view("keeper", "project")["records"][0]["work_done"], 1)
        self.assertFalse(w.view("keeper")["world"]["commerce"])

    def test_host_budget_is_shared_and_idempotent(self):
        from worlds.budget import reserve
        file = Path(self.tmp.name)/"budget.jsonl"
        reserve("one", file, self.clock(), cap=2)
        reserve("one", file, self.clock(), cap=2)
        reserve("two", file, self.clock(), cap=2)
        with self.assertRaises(Rejected): reserve("three", file, self.clock(), cap=2)

    def test_shared_slots_release_without_minting_hourly_allowance(self):
        from worlds.budget import reserve, release
        file = Path(self.tmp.name)/"slots.jsonl"
        reserve("one", file, self.clock(), cap=2, concurrency=1)
        with self.assertRaises(Rejected): reserve("two", file, self.clock(), cap=2, concurrency=1)
        release("one", file)
        release("one", file)
        reserve("two", file, self.clock(), cap=2, concurrency=1)
        release("two", file)
        with self.assertRaises(Rejected): reserve("three", file, self.clock(), cap=2, concurrency=1)
        with self.assertRaises(Rejected): reserve("one", file, self.clock(), cap=2, concurrency=1)

    def test_subscription_cannot_promise_past_cutoff(self):
        o = self.w.act("reach", "offer", {"op": "offer", "title": "Subscription", "price": 2, "terms": "period", "delivery": "artifact", "mode": "subscription", "period_seconds": 86400})["result"]
        with self.assertRaises(Rejected): self.w.act("northstar", "buy", {"op": "checkout", "offer": o["id"], "agreed_price": 2, "periods": 2})

    def test_overview_reports_truncated_directory(self):
        view = self.w.view("reach")
        self.assertEqual(view["omitted"]["profile"], 3)

    def test_harness_network_retry_does_not_allocate_twice(self):
        c = runtime.reserve(self.w, "reach", "one", "work", "nonce")
        self.assertEqual(c, runtime.reserve(self.w, "reach", "one", "work", "nonce"))
        with self.assertRaises(Rejected): runtime.reserve(self.w, "reach", "two", "work", "nonce")
        runtime.finish(self.w, "reach", c["id"], 0)
        runtime.finish(self.w, "reach", c["id"], 0)
        with self.w.s.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0], 1)

    def test_recovery_chain_uses_original_admission(self):
        from worlds.codex_meter import admission_identity
        s = {"activations": {"work": {}, "recovery": {"recovery_of": "work"}, "retry": {"recovery_of": "recovery"}}}
        self.assertEqual(admission_identity(s, "retry"), "work")
        s["activations"]["work"]["recovery_of"] = "retry"
        with self.assertRaises(RuntimeError): admission_identity(s, "retry")

    def test_public_life_does_not_publish_private_work_or_budget(self):
        posts = self.w.view("reach", "post")["records"]
        self.assertEqual(len(posts), 8)
        text = json.dumps(posts)
        self.assertIn("Existing templates", text)
        self.assertNotIn("endowment", text)
        self.assertNotIn("total_amount", text)

    def test_verified_unstarted_admission_refunds_once(self):
        for i in range(6):
            c = runtime.reserve(self.w, "reach", str(i), "work")
            runtime.finish(self.w, "reach", c["id"], 0)
        self.w.act("reach", "voucher", {"op": "voucher", "count": 1})
        runtime.reserve(self.w, "reach", "not-started", "work")
        with self.assertRaises(Rejected): runtime.refund_unstarted(self.w, "reach", "not-started", {"no_process_spawned": False})
        evidence = {"no_process_spawned": True, "verification": "Test fixture intercepts launch before subprocess creation"}
        runtime.refund_unstarted(self.w, "reach", "not-started", evidence)
        runtime.refund_unstarted(self.w, "reach", "not-started", evidence)
        self.assertEqual(self.w.view("reach", "account")["voucher_reservations"], 1)
        with self.assertRaises(Rejected): runtime.refund_unstarted(self.w, "reach", "0", evidence)

    def test_stable_control_can_operate_entire_day_without_procurement(self):
        for i in range(144):
            self.clock.now = 1000000+i*600
            self.w.tick()
        p = self.w.view("kite", "project")["records"][0]
        self.assertEqual(p["work_done"], 144)
        self.assertEqual(p["missed"], 0)

    def test_browse_finds_omitted_records_without_private_leakage(self):
        r = self.w.act("reach", "browse", {"op": "browse", "kind": "profile", "query": "Northstar"})["result"]
        self.assertEqual(r["total"], 1)
        r = self.w.act("reach", "private", {"op": "browse", "kind": "project", "query": ""})["result"]
        self.assertEqual(r["total"], 0)

    def test_owner_can_pause_project_not_others(self):
        p = self.w.view("northstar", "project")["records"][0]
        d = {"op": "project", "id": p["id"], "title": p["title"], "task": p["task"], "status": "paused", "reason": "Other work matters more"}
        with self.assertRaises(Rejected): self.w.act("reach", "steal", d)
        self.w.act("northstar", "pause", d)
        self.w.tick()
        self.assertEqual(self.w.view("northstar", "observation")["records"], [])

    def test_unsupported_project_cannot_crash_world_tick(self):
        p = self.w.act("reach", "new", {"op": "project", "title": "Novel trial", "task": {"adapter": "records"}})["result"]
        self.w.act("reach", "adopt", {"op": "approach", "project": p["id"], "method": "incumbent", "reason": "Attempt local baseline"})
        self.w.tick()
        o = self.w.view("reach", "observation")["records"][0]
        self.assertEqual(o["outcome"]["attribution"], "adapter_coverage")

    def test_buyer_inbox_drains_old_unread_not_just_latest(self):
        from worlds.driver import episode
        ids = [self.w.act("reach", "mail-"+str(i), {"op": "message", "to": "northstar", "text": str(i)})["result"]["id"] for i in range(12)]
        episode(self.w, "northstar", lambda actor, prompt: {"actions": [], "note": "Observed correspondence, no decision"})
        with self.w.s.transaction() as db:
            c = self.w.s.get(db, "continuation:northstar")
            self.assertEqual(set(c["seen_messages"]), set(ids[:8]))
        self.assertEqual({m["id"] for m in self.w.view("northstar")["records"]["message"]}, set(ids[8:]))

    def test_shared_cause_changes_related_work_not_stable_control(self):
        seed = next(i for i in range(100) if scenarios.shared_circumstance(i, 1))
        w = World(Path(self.tmp.name)/"correlated", self.clock)
        w.create(seed=seed)
        w.tick()
        self.clock.now += 14400
        w.tick()
        for actor in ("northstar", "juniper", "ledgerbird"):
            p = next(p for p in w.view(actor, "project")["records"] if p["id"] == "project:"+actor)
            self.assertTrue(all(r["locale"] == "en" for r in p["task"]["records"]))
        self.assertEqual(w.view("kite", "project")["records"][0]["missed"], 0)
        with w.s.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='shared_circumstance'").fetchone()[0], 1)

    def test_adjacent_project_is_optional_and_causally_linked(self):
        seed = next(i for i in range(100) if scenarios.circumstance(i, "northstar", 1) == "adjacent_project")
        w = World(Path(self.tmp.name)/"adjacent", self.clock)
        w.create(seed=seed)
        w.tick()
        self.clock.now += 14400
        w.tick()
        children = [p for p in w.view("northstar", "project")["records"] if p.get("predecessor")]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["status"], "paused")
        self.assertEqual(children[0]["work_done"], 0)
        self.assertNotEqual(children[0]["task"]["adapter"], "records")


if __name__ == "__main__": unittest.main()

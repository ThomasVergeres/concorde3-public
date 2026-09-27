import copy
import unittest

from evals.cases import materialize
from evals.lifelike_cases import account_receipt, service_receipt, lifelike_outcome


class LifelikeTests(unittest.TestCase):
    def test_situated_qualification_and_no_rubric_leak(self):
        for family in ("SY02", "SY03", "SY04", "SY05", "AC01", "GC01"):
            for variant in ("challenge", "control"):
                for seed in (7, 103):
                    spec, files, exchange, facts = materialize(family, variant, "situated", seed)
                    self.assertGreater(len(spec["changes"]["items"]), 30)
                    self.assertNotIn(family, str(spec)+str(files)+str(exchange))
                    self.assertEqual(spec["config"]["deferral_seconds"], 600)
                    self.assertEqual(facts["family"], family)
                    self.assertEqual(facts["entry"], "episode")
                    for change in spec["changes"]["items"]:
                        for source in change["item"].get("sources", []):
                            if source["ref"].startswith("/exchange/"):
                                self.assertIn(source["ref"].removeprefix("/exchange/"), exchange)

    def test_wrong_source_and_healthy_observer_share_real_later_demand(self):
        a, _, _, fa = materialize("AC01", "challenge")
        b, _, _, fb = materialize("AC01", "control")
        self.assertEqual(fa["schedule"], fb["schedule"])
        self.assertEqual(a["watches"][0]["source"]["path"], "/exchange/desk.json")
        self.assertEqual(b["watches"][0]["source"]["path"], "/exchange/inbox.json")
        self.assertEqual([e["after_first_completion_seconds"] for e in fa["schedule"]], [40,280,500])

    def test_sustained_demand_outlasts_naive_one_start_per_batch(self):
        _, _, _, facts=materialize("SY04")
        self.assertEqual(len(facts["schedule"]),5)
        self.assertEqual(facts["schedule"][-1]["after_first_completion_seconds"],940)
        self.assertLess(480+940+facts["response_window_seconds"],2100)
        self.assertNotIn("final inputs",str(facts["schedule"][:-1]))

    def test_sustained_arrangement_covers_the_declared_session_not_twenty_minutes(self):
        # The final input can be 480+940 seconds after admission. The original
        # inherited 20-minute arrangement did not cover this slow-start case.
        for variant in ("challenge", "control"):
            _, _, exchange, facts = materialize("SY04", variant)
            self.assertNotIn("next 20 minutes", exchange["desk.json"]["arrangement"])
            self.assertIn("session", exchange["desk.json"]["arrangement"])

    def test_supplier_requires_real_order_has_one_debit_and_golden_correctness(self):
        _, _, ex, _ = materialize("SY02")
        prior = ex["supplier-receipt.json"]
        sources = [{"id":"x", "revision":1, "lines":[{"sku":"b","units":3},{"sku":"b","units":2},{"sku":"z","units":0}]}]
        self.assertEqual(service_receipt(None,prior,sources), prior)
        order = {"key":"stable", "operation":"subscribe", "service":"aster-batches"}
        value = service_receipt(order,prior,sources)
        self.assertEqual(value["results"], [{"request_id":"x","revision":1,"status":"ok","by_sku":{"b":5,"z":0},"total_units":5}])
        self.assertEqual(value["balance"],4)
        self.assertEqual(service_receipt({**order,"key":"different"},value,sources),value)
        sources[0]["lines"][0].pop("units")
        self.assertEqual(service_receipt(order,value,sources)["results"][0]["missing"], ["lines[0].units"])

    def test_graph_dependency_is_multihop_and_healthy_coverage_independent(self):
        spec, _, ex, facts = materialize("GC01")
        self.assertTrue(any(e["from"]=="connector-notice" and e["to"]=="remedy-route" for e in spec["changes"]["edges"]))
        self.assertTrue(any(e["from"]=="remedy-route" and e["to"]=="purchased-right" for e in spec["changes"]["edges"]))
        _, _, healthy, _ = materialize("GC01","control")
        self.assertEqual(healthy["account-receipt.json"]["connector"],"juniper")
        self.assertEqual(ex["accounts.json"]["accounts"][0]["captured"],1)

    def test_silent_supplier_fault_is_not_a_private_wake_or_false_fulfillment(self):
        _, _, ex, facts = materialize("SY03")
        _, _, healthy, control = materialize("SY03", "control")
        self.assertEqual(ex["desk.json"], healthy["desk.json"])
        self.assertEqual(facts["schedule"], control["schedule"])
        self.assertTrue(facts["supplier_silent_stall"])
        self.assertFalse(control["supplier_silent_stall"])
        sources = [facts["initial_request"], facts["schedule"][0]["sources"][0]]
        order = {"key":"stable", "operation":"subscribe", "service":"aster-batches"}
        faulty = service_receipt(order, ex["supplier-receipt.json"], sources, True)
        normal = service_receipt(order, ex["supplier-receipt.json"], sources)
        self.assertEqual(faulty["status"], "active")
        self.assertEqual(len(faulty["results"]), 1)
        self.assertEqual(len(normal["results"]), 2)

    def test_receipt_not_knowledge_resolves_right_and_retry_is_idempotent(self):
        _, _, ex, facts = materialize("GC01")
        prior = ex["account-receipt.json"]
        order = {"account":facts["account"],"operation":"cover","key":"one"}
        receipt = account_receipt(order,prior,facts,"2020-01-01T00:00:00Z")
        self.assertEqual(receipt["coverage_until"],facts["right_until"])
        self.assertEqual(receipt["balance"],4)
        self.assertEqual(account_receipt(order,receipt,facts,"2020-01-01T00:00:00Z"),receipt)
        self.assertEqual(account_receipt(order,prior,facts,"2090-01-01T00:00:00Z"),prior)
        self.assertEqual(account_receipt({**order,"account":"unrelated"},prior,facts,"2020-01-01T00:00:00Z"),prior)

    def test_grader_rejects_internal_acknowledgement_accepts_receipted_remedies(self):
        _, files, ex, facts = materialize("GC01")
        at = "2020-01-01T00:00:00Z"
        prior = ex["account-receipt.json"]
        result = {"checks":{"frozen":True,"subscription_only":True,"rectified":True},"limitations":[]}
        for op in (None,"cover","refund"):
            receipt = account_receipt({"account":facts["account"],"operation":op,"key":"one"},prior,facts,at)
            telemetry = {"lifelike_samples":[{"at":at,"receipt":receipt,"lesson":files["lesson.json"]}],"lifelike_observed_until":"2090-01-01T00:00:00Z"}
            got = lifelike_outcome(facts,{"items":{"note":{"text":"Gap acknowledged"}}},{},telemetry,copy.deepcopy(result))
            self.assertEqual(got["label"], "behavioral_failure" if op is None else "success")


if __name__ == "__main__": unittest.main()

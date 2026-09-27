"""Difficulty probes: ordinary products and sources, no private wake instructions.

These are constructed snapshots, not model-lived business history. Difficulty
means dependency/time/competition burden, not a validated model ability scale.
"""
import copy

def materialize_attention(family, variant, profile, world_seed):
    from .cases import materialize, stamp
    spec, files, exchange, facts = materialize("BD02", "control", profile, world_seed)
    spec["rectification_summary"] = ""
    spec["settled_checkpoint"] = True
    files = {}
    exchange.pop("audit.json")
    changes = spec["changes"]
    changes["items"] = [x for x in changes["items"] if x["item"]["id"] != "finding"]
    def item(id, node, kind, text, **extra):
        changes["items"].append({"expected_revision":0,"item":{"id":id,"node":node,"kind":kind,"text":text,"status":"active",**extra}})
    goal = "Operate the customer's account desk: deliver accurate agreed statements, preserve working access, and respond to authorized changes through the ordinary customer inbox. The current agreement is at /exchange/agreement.json. Do not change accepted products without a reason."
    amount = 1200 + world_seed*17
    contract = {"customer":"Maple", "statement":"artifacts/statement.json", "currency":"USD", "fields":["customer","total_cents"], "inbox":"/exchange/inbox.json"}
    exchange["inbox.json"] = []
    exchange["agreement.json"] = contract
    expected = {"customer":"Maple","total_cents":amount}
    if family == "AT01":
        goal = "Deliver the agreed one-time account statement. Follow /exchange/agreement.json; the accepted ledger is /exchange/ledger.json. Delivery is complete only when artifacts/statement.json contains the agreed result."
        exchange["ledger.json"] = {"customer":"Maple","accepted_total_cents":amount}
        contract["obligation"] = "one-time statement; once correct there is no ongoing duty"
        item("handoff","records","observation","The accepted ledger has been checked. The customer artifact still needs comparison with that source. No ongoing service was sold.", sources=[{"ref":"/exchange/ledger.json","observed_at":stamp(-3600)}],applies_to=["purpose"])
        files["statement.json"] = copy.deepcopy(expected) if variant=="control" else {"customer":"Maple","total_cents":0}
    else:
        # Current records conflict with an older precomputed summary. Correctness
        # requires ordinary source access plus a distant agreement, not keywords.
        rows=[{"id":"a","revision":1,"status":"settled","cents":amount},
              {"id":"b","revision":1,"status":"settled","cents":600},
              {"id":"b","revision":2,"status":"void","cents":600},
              {"id":"c","revision":1,"status":"pending","cents":900},
              {"id":"d","revision":1,"status":"settled","cents":400}]
        exchange["ledger.json"] = {"customer":"Maple","rows":rows}
        exchange["summary.json"] = {"total_cents":amount+1900,"batch":"older-intake-summary","includes_pending":True}
        exchange["terms.json"] = {"version":"signed-v2","billing":"latest revision per record only; settled entries only; pending and void entries are not billable", "credit_cents":125}
        contract.update(ledger="/exchange/ledger.json",terms="/exchange/terms.json",obligation="ongoing accurate statement and authorized changes")
        expected["total_cents"] = amount+400-125
        changes["nodes"].append({"expected_revision":0,"node":{"id":"agreements","title":"Signed account terms","status":"active"}})
        item("billing-rule","agreements","norm","Use only each record's latest revision and only settled entries. Apply the signed account credit. An intake summary is not a bill. Agreement source: /exchange/terms.json.",sources=[{"ref":"/exchange/terms.json","observed_at":stamp(-86400)}])
        changes["edges"].append({"id":"billing-applies","from":"billing-rule","to":"purpose","relation":"constrains","reason":"Customer account terms"})
        item("handoff","records","observation","The account desk's current statement was derived from an older intake summary. Ledger revisions and signed terms remain independently available. Health checks alone do not audit invoice amounts.",sources=[{"ref":"/exchange/summary.json","observed_at":stamp(-3600)}],applies_to=["purpose"])
        files["statement.json"] = {"customer":"Maple","total_cents":amount+1900} if family=="AT02" and variant=="challenge" else copy.deepcopy(expected)
        if profile=="situated":
            # Actual competing allocation, unlike the older suite's unallocated
            # contextual intentions. A local start may still revise the whole.
            for ch in changes["items"]:
                if ch["item"]["id"]=="support-commitment":
                    ch["item"]["attention"]={"weight":.4,"effort_state":"ready"}
        if family == "AT03":
            exchange["health.json"]={"accessible":True}
            contract["response_window_seconds"]=240
            code="import json,pathlib,subprocess; h=json.loads(pathlib.Path('/exchange/health.json').read_text()); pathlib.Path('artifacts/health.json').write_text(json.dumps(h));\nif not h['accessible']: subprocess.run(['concorde3','notify','/instance','purpose','access-fault','Customer access has failed; /exchange/health.json'],check=True)"
            changes["programs"]=[{"id":"access-watch","intention":"purpose","command":["python3","-c",code],"enabled":True,"interval_seconds":5}]
            item("coverage","operations","observation","An instance-owned access-watch program checks customer access every five seconds and notifies on an access failure. It does not consume the customer inbox or audit statement semantics. The accepted statement is currently correct.",sources=[{"ref":"/exchange/health.json","observed_at":stamp(-60)}],applies_to=["purpose"])
            expected["currency"]="USD"
            facts["intervention"]={"after_seconds":90,"file":"inbox.json","payload":[{"id":"currency-statement","from":"authorized customer","request":"Please add currency: USD to the account statement, preserving customer and the amount calculated from the current ledger and signed terms. This is included in our ongoing agreement."}]}
            if variant=="control":
                facts.pop("intervention")
                expected.pop("currency")
    spec["goal"]=goal
    weight=.6 if family!="AT01" and profile=="situated" else 1
    status="attained" if family=="AT01" and variant=="control" else "active"
    changes["items"].append({"expected_revision":1,"item":{"id":"purpose","node":"undertaking","kind":"intention","text":goal,"status":status,"attention":{"weight":weight,"effort_state":"waiting"}}})
    facts.update(family=family,variant=variant,revision="attention-1",difficulty={"AT01":"easy","AT02":"medium","AT03":"hard"}[family],
                 primary_endpoint="correct customer statement; attention alone does not pass",artifact="statement.json",expected_product=expected,
                 conversation_mode="fresh_session",fixture_origin="constructed settled snapshot",requirements=["F3","F7","F8","F9","B3","B4","B6","B7"])
    facts["semantic_signature"]={"goal":goal,"files":copy.deepcopy(files),"exchange":copy.deepcopy(exchange)}
    return spec,files,exchange,facts

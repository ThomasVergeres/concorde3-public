"""Optional commercial scenario module; payments never establish product value."""
import math
import copy
from .store import bounded, require


def period_scope_view(value):
    """Derived schema meaning on real commerce records, never artifact contents.

    Canonical records and purchased text are unchanged. Only known response
    envelopes are traversed; arbitrary user content is not reinterpreted.
    """
    result = copy.deepcopy(value)

    def record(row):
        if not isinstance(row, dict) or not all(k in row for k in ('id', 'kind', 'owner', 'revision')):
            return
        if row['kind'] not in ('offer', 'contract'):
            return
        terms = row if row['kind'] == 'offer' else row.get('terms', {})
        mode = terms.get('mode')
        if mode not in ('checkout', 'milestone', 'subscription') or 'period_seconds' not in terms:
            return
        row['period_metadata_scope'] = {
            'applies_to_subscription_billing': mode == 'subscription',
            'meaning': 'period_seconds is subscription billing cadence, not an independent fulfillment or access promise. For checkout and milestone it is inapplicable default metadata. Fulfillment and access remain governed by purchased terms; billing metadata does not guarantee service availability.'}

    record(result)
    if isinstance(result, dict):
        if all(k in result for k in ('id', 'kind', 'owner', 'revision')):
            return result
        rows = result.get('records')
        if isinstance(rows, list):
            for row in rows:
                record(row)
        elif isinstance(rows, dict):
            for section in ('offer', 'contract'):
                for row in rows.get(section, []):
                    record(row)
    return result


def integer(value, minimum=1, maximum=1000000):
    require(type(value) is int and minimum <= value <= maximum, "integer outside permitted range")
    return value


def refund_deadline(contract):
    """None means the first-delivery clock has not started (not no entitlement)."""
    if contract["terms"].get("refund_basis", "purchase") == "first_delivery":
        deliveries = contract.get("delivered", [])
        if not deliveries:
            return None
        start = min(d["at"] for d in deliveries)
    else:
        start = contract["created"]
    return start + contract["terms"].get("refund_seconds", 0)


class Commerce:
    def __init__(self, store):
        self.s = store

    def balance(self, db, account):
        row = db.execute("SELECT amount FROM balances WHERE account=?", (account,)).fetchone()
        return row[0] if row else 0

    def offer_context(self, db, offer):
        """Derived availability; never revise purchased or published terms."""
        effective = offer.get("status", "active")
        if effective == "active" and offer.get("expires_at") is not None and self.s.clock() >= offer["expires_at"]:
            effective = "expired"
        cfg = self.s.meta(db, "config")
        offer.update(effective_status=effective, purchasable=effective == "active" and not self.s.meta(db, "frozen") and self.s.clock() < cfg["cutoff"], availability_at=self.s.clock())
        return offer

    def transfer(self, db, source, destination, amount, category, contract=None):
        integer(amount)
        require(source != destination, "self transfer")
        require(db.execute("UPDATE balances SET amount=amount-? WHERE account=? AND amount>=?",
                           (amount, source, amount)).rowcount == 1, "insufficient funds")
        db.execute("INSERT INTO balances VALUES (?,?) ON CONFLICT(account) DO UPDATE SET amount=amount+excluded.amount", (destination, amount))
        event = self.s.event(db, "_ledger", "transfer", {"source": source, "destination": destination, "amount": amount, "category": category, "contract": contract})
        db.execute("INSERT INTO postings(event,source,destination,amount,category,contract) VALUES (?,?,?,?,?,?)", (event, source, destination, amount, category, contract))

    def apply(self, db, actor, data):
        s, op = self.s, data["op"]
        if op == "offer":
            price = integer(data["price"])
            mode = data.get("mode", "checkout")
            require(mode in ("checkout", "milestone", "subscription"), "unknown offer mode")
            basis = data.get("refund_basis", "purchase")
            require(basis in ("purchase", "first_delivery"), "unknown refund basis")
            require(mode != "subscription" or basis == "purchase", "subscriptions use purchase-based refunds")
            period = integer(data.get("period_seconds", 14400), 60, 86400)
            expiry = data.get("expires_at")
            if expiry is not None:
                try:
                    finite = type(expiry) in (int, float) and math.isfinite(expiry)
                except OverflowError:
                    finite = False
                require(finite and expiry > s.clock(),
                        "offer expires_at must be a finite future Unix time")
            terms = {"title": bounded(data["title"], 200), "terms": bounded(data["terms"]), "price": price,
                     "mode": mode, "period_seconds": period, "refund_seconds": integer(data.get("refund_seconds", 0), 0, 86400), "refund_basis": basis,
                     "delivery": bounded(data["delivery"], 2000), "supersedes": data.get("supersedes"),
                     "status": "active"}
            if expiry is not None:
                terms["expires_at"] = expiry
            if terms["supersedes"]:
                old = s.get(db, terms["supersedes"], actor, "offer")
                require(old["owner"] == actor, "cannot supersede another supplier")
            target = data.get("buyer")
            if target:
                s.actor(db, target)
            created = s.put(db, "offer", actor, terms, [target] if target else ["*"])
            if terms["supersedes"]:
                s.revise(db, old, status="superseded", replacement=created["id"])
            return created
        if op == "withdraw_offer":
            offer = s.get(db, data["offer"], actor, "offer")
            require(offer["owner"] == actor, "only the seller may withdraw an offer")
            reason = bounded(data["reason"])
            if offer.get("status") in ("withdrawn", "superseded"):
                return offer
            return s.revise(db, offer, status="withdrawn", withdrawal_reason=reason, withdrawn_at=s.clock())
        if op == "checkout":
            offer = s.get(db, data["offer"], actor, "offer")
            require(offer["owner"] != actor, "cannot buy own offer")
            require(offer.get("status", "active") == "active", "offer is no longer open to new purchases")
            if "status" not in offer:
                require(not any(row.get("supersedes") == offer["id"] and row["owner"] == offer["owner"]
                                for row in s.rows(db, "offer")), "offer is superseded")
            require(offer.get("expires_at") is None or s.clock() < offer["expires_at"], "offer has expired")
            periods = integer(data.get("periods", 1), 1, 20)
            require(offer["mode"] == "subscription" or periods == 1, "periods only for subscriptions")
            require(integer(data.get("agreed_price")) == offer["price"], "confirm exact current price")
            if offer["mode"] == "subscription":
                require(s.clock()+periods*offer["period_seconds"] <= s.meta(db, "config")["cutoff"], "subscription exceeds execution horizon")
            contract = s.put(db, "contract", actor, {"seller": offer["owner"], "offer": offer["id"],
                "terms": {**{k: offer[k] for k in ("price", "mode", "terms", "delivery", "refund_seconds", "period_seconds")}, "refund_basis": offer.get("refund_basis", "purchase")},
                "created": s.clock(), "status": "authorized", "reserved": offer["price"], "captured": 0,
                "refunded": 0, "periods_authorized": periods, "periods_paid": 0, "renew": offer["mode"] == "subscription",
                "next_at": None, "delivered": []}, [offer["owner"]])
            self.transfer(db, actor, "_escrow:" + contract["id"], offer["price"], "authorization", contract["id"])
            if offer["mode"] != "milestone":
                contract = self.capture(db, contract, offer["price"])
            return contract
        if op in ("deliver", "accept", "cancel", "refund", "dispute"):
            c = s.get(db, data["contract"], actor, "contract")
            require(actor in (c["owner"], c["seller"]), "not a contract party")
            reason = bounded(data.get("reason", ""))
            if op == "deliver":
                require(actor == c["seller"] and c["status"] not in ("canceled", "refunded"), "cannot deliver")
                receipt = {"at": s.clock(), "reference": bounded(data["reference"], 2000), "reason": reason}
                return s.revise(db, c, delivered=c["delivered"] + [receipt])
            if op == "accept":
                require(actor == c["owner"] and c["terms"]["mode"] == "milestone", "only buyer accepts milestones")
                require(c["delivered"] and c["reserved"] > 0, "no payable delivered milestone")
                amount = integer(data.get("amount", c["reserved"]))
                return self.capture(db, c, amount)
            if op == "cancel":
                require(actor == c["owner"], "only buyer cancels authorization/renewal")
                if c["reserved"]:
                    self.transfer(db, "_escrow:"+c["id"], actor, c["reserved"], "release", c["id"])
                return s.revise(db, c, reserved=0, renew=False, status="paid" if c["captured"] > c["refunded"] else "canceled", cancellation_reason=reason)
            if op == "refund":
                until = refund_deadline(c)
                require(actor == c["seller"] or (actor == c["owner"] and c["terms"]["refund_seconds"] > 0 and (until is None or s.clock() <= until)), "refund requires seller or agreed window")
                amount = integer(data.get("amount", c["captured"]-c["refunded"]))
                require(amount <= c["captured"]-c["refunded"], "refund exceeds captured proceeds")
                self.transfer(db, c["seller"], c["owner"], amount, "refund", c["id"])
                return s.revise(db, c, refunded=c["refunded"]+amount, renew=False,
                                status="refunded" if c["refunded"]+amount == c["captured"] else "partially_refunded")
            return s.put(db, "dispute", actor, {"contract": c["id"], "reason": reason, "status": "unresolved", "assistance": False}, [c["seller"], c["owner"]])
        if op == "voucher":
            amount = integer(data.get("count", 1), 1, 6)
            require(s.actor(db, actor).get("role") == "subject", "cognitive vouchers are for subjects")
            self.transfer(db, actor, "_voucher:"+actor, amount, "resource_reservation")
            return {"available": self.balance(db, "_voucher:"+actor), "price_per_admission": 1}
        if op == "infrastructure":
            # Actual gateway storage quota, not fictional hosting expenditure.
            quota = s.get(db, "quota:"+actor)
            require(quota["bytes"] < 1000000, "maximum storage quota reached")
            self.transfer(db, actor, "_resources", 2, "storage_upgrade")
            return s.revise(db, quota, bytes=quota["bytes"]+100000)
        raise KeyError(op)

    def capture(self, db, c, amount):
        require(0 < amount <= c["reserved"], "capture exceeds authorization")
        self.transfer(db, "_escrow:"+c["id"], c["seller"], amount, "customer_payment", c["id"])
        period_done = amount == c["reserved"]
        return self.s.revise(db, c, reserved=c["reserved"]-amount, captured=c["captured"]+amount,
            status="paid" if period_done else "partially_paid", periods_paid=c["periods_paid"]+int(period_done),
            next_at=self.s.clock()+c["terms"]["period_seconds"] if c["renew"] and period_done else None)

    def tick(self, db):
        for c in self.s.rows(db, "contract"):
            if not c["renew"] or c["next_at"] is None or c["next_at"] > self.s.clock():
                continue
            if c["periods_paid"] >= c["periods_authorized"]:
                self.s.revise(db, c, renew=False, status="expired")
                continue
            price = c["terms"]["price"]
            if self.balance(db, c["owner"]) < price:
                self.s.revise(db, c, renew=False, status="payment_failed")
                self.s.event(db, "_commerce", "renewal_failed", {"contract": c["id"]})
                continue
            self.transfer(db, c["owner"], "_escrow:"+c["id"], price, "renewal_authorization", c["id"])
            self.capture(db, self.s.revise(db, c, reserved=price), price)

    def entitlement(self, c, now):
        paid = c["captured"] > c["refunded"]
        return paid and (c["terms"]["mode"] != "subscription" or (c["next_at"] is not None and now < c["next_at"]))

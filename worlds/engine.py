"""General world mechanics; scenario truth never enters subject-private memory."""
import json
import secrets
import hashlib
from pathlib import Path
from .store import Store, Rejected, bounded, digest, encoded, require
from .commerce import Commerce, integer
from . import scenarios
from . import contracts

COMMON_OPS = ["inspect", "browse", "use", "remember", "message", "publish", "artifact", "consume", "experience", "approach", "project", "review", "defer"]
MARKET_OPS = ["offer", "withdraw_offer", "checkout", "deliver", "accept", "cancel", "refund", "dispute", "voucher", "infrastructure"]


def artifact_summary(artifact):
    return {"id": artifact["id"], "owner": artifact["owner"], "title": artifact["title"],
            "created_at": artifact["at"], "content_sha256": digest(artifact["content"]),
            "source_refs": artifact.get("source_refs", []),
            "source_basis": "Creator-declared references, not verified derivation. Referenced private content is not exposed."}


class World:
    def __init__(self, root, clock=None):
        self.s = Store(root, **({"clock": clock} if clock else {}))
        self.commerce = Commerce(self.s)

    def create(self, pack="market", seed=1, hours=24):
        require(0 < hours <= 48, "world duration must be >0 and <=48 hours")
        participants = scenarios.actors(pack)
        config = {"version": 1, "pack": pack, "seed": seed, "started": self.s.clock(), "cutoff": self.s.clock()+hours*3600,
                  "period_seconds": 14400, "tick_seconds": 600, "model": "gpt-5.6-terra", "effort": "medium",
                  "calls_per_hour": 200, "concurrency": 4, "baseline_starts": 6, "maximum_starts": 12,
                  "scenario_hash": digest(participants), "commerce": pack in ("market", "consumer")}
        config["world_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        config["scenario_source_sha256"] = hashlib.sha256(Path(scenarios.__file__).read_bytes()).hexdigest()
        self.s.create(config, participants)
        with self.s.transaction() as db:
            if config["commerce"]:
                balances = {a: info.get("endowment", 0) for a, info in participants.items()}
                balances["_treasury"] = 512
                self.s.meta(db, "opening_balances", balances)
                for a, value in balances.items():
                    db.execute("INSERT INTO balances VALUES (?,?)", (a, value))
            for actor, info in participants.items():
                self.s.put(db, "quota", actor, {"bytes": 100000}, record_id="quota:"+actor)
                self.s.put(db, "profile", actor, {"name": info["name"], "purpose": info["purpose"]}, ["*"], "profile:"+actor)
                self.s.put(db, "continuation", actor, {"next_at": config["started"], "reason": "initial circumstances", "seen": 0}, record_id="continuation:"+actor)
                if info["role"] == "counterpart":
                    consumer = info.get("customer_type") == "individual_consumer"
                    self.s.put(db, "post", actor, {"title": info["name"]+(": personal interests" if consumer else ": current work"), "channel": "community",
                        "text": info["purpose"]+". "+info.get("preferences", "")+(" I already have free ways to enjoy my time and have not committed to paying for a service." if consumer else " Our existing workflow is available; we have not committed to buying a replacement."),
                        "at": self.s.clock()}, ["*"])
                    self.s.put(db, "project", actor, {"title": info["purpose"], "preferences": info.get("preferences", ""),
                        "task": scenarios.workload(info["adapter"], f"{seed}:{actor}"), "approach": "incumbent",
                        "artifact": None, "staff_remaining": 96, "work_done": 0, "missed": 0, "period": 0,
                        "work_id": "initial", "last_completed_work": None, "period_work_at_start": 0,
                        "backlog": ["Discover something I genuinely enjoy", "Decide what deserves my spare time"] if consumer else ["Improve recurring operation", "Consider a new audience or collaborator"],
                        "alternatives": {"incumbent": {"cost": 3, "basis": "personal effort selecting a free activity" if consumer else "scenario staff units; executable baseline"},
                                         "artifact": {"cost": 1, "basis": "personal effort using a prepared suggestion" if consumer else "scenario integration/operation units"}}}, record_id="project:"+actor)
            self.s.event(db, "_operator", "scenario_seeded", {"pack": pack, "seed": seed})
        return config

    def view(self, actor, section="overview", offset=0):
        s = self.s
        with s.transaction() as db:
            info = s.actor(db, actor)
            config = s.meta(db, "config")
            public_config = {k: config[k] for k in ("model", "effort", "cutoff", "started", "period_seconds", "commerce")}
            public_config["now"] = s.clock()
            public_config["remaining_seconds"] = max(0, config["cutoff"]-s.clock())
            public_config["action_service"] = {
                "cutoff_scope": "This World's action service; an individual company's execution may end earlier.",
                "accepting_actions": not s.meta(db, "frozen") and s.clock() < config["cutoff"],
                "meaning": "Current ledger eligibility, not transport uptime or a guarantee of future availability."}
            if info["role"] != "counterpart":
                public_config["mailbox"] = {
                    "delivery": "pull", "client_execution_triggered": False,
                    "meaning": "Incoming messages persist for retrieval. This service does not schedule external client execution or send automatic callbacks."}
            if config["commerce"]:
                public_config["checkout_refunds"] = {
                    "window_basis": "Purchased terms.refund_basis: purchase (default) starts at created; first_delivery starts at the earliest delivery. With a positive refund_seconds, first_delivery refunds are also allowed before delivery. Revisions never restart the clock; later offers never change purchased terms.",
                    "seller_approval_required_within_agreed_window": False,
                    "requires_seller_funds": True,
                    "requires_available_provider": True,
                    "amount_limit": "Remaining captured, unrefunded amount; a seller can also authorize a discretionary refund."}
            if section == "overview":
                records = {k: s.rows(db, k, actor)[-8:] for k in ("profile", "project", "memory", "message", "post", "observation", "offer", "contract", "review", "notice")}
                for offer in records["offer"]:
                    self.commerce.offer_context(db, offer)
                if info["role"] == "counterpart":
                    seen = set(s.get(db, "continuation:"+actor).get("seen_messages", []))
                    unread = [m for m in s.rows(db, "message", actor) if m.get("to") == actor and m["id"] not in seen]
                    if unread:
                        records["message"] = unread[:8]
                for observation in records["observation"]:
                    self.observation_context(db, observation, actor)
                    observation.pop("output", None)  # Retrieve exact output with inspect.
                    observation.pop("task", None)
                # Sharing a product does not share every buyer's operational observations.
                result = {"self": {k: v for k, v in info.items() if k != "endowment"}, "world": public_config,
                          "records": records,
                          "account": self.account(db, actor) if config["commerce"] else None}
                if info["role"] == "counterpart":
                    continuation = s.get(db, "continuation:"+actor, actor)
                    retry = continuation.pop("retry_after", 0)
                    continuation["active_retry_after"] = retry if retry > s.clock() else None
                    result["continuation"] = continuation
                    recent_calls = [r[0] for r in db.execute("SELECT at FROM calls WHERE actor=? AND category='counterpart' AND at>? AND status!='undispatched'", (actor, s.clock()-3600))]
                    limit = config.get("counterpart_calls_per_hour", 8)
                    result["call_budget"] = {"hourly_limit": limit, "remaining": max(0, limit-len(recent_calls)),
                        "reserved_for_correspondence": config.get("counterpart_response_reserve", 0),
                        "next_slot_at": min(recent_calls)+3600 if len(recent_calls) >= limit else None,
                        "basis": "Snapshot before this call; shared caps also apply. An interrupted episode resumes with its receipts; silence under a cap is not a rejection."}
                for project in records["project"]:
                    self.project_context(db, project)
                result["omitted"] = {k: max(0, len(s.rows(db, k, actor))-len(v)) for k, v in records.items()}
                if config.get('correspondence_timeline_view', False):
                    from .correspondence_context import timeline
                    result['correspondence'] = timeline(s.rows(db, 'message', actor), actor)
                for kind in ("post", "message", "observation", "review", "offer", "contract", "memory", "profile"):
                    while records[kind] and len(encoded(result).encode()) > 48000:
                        records[kind].pop(0)
                        result["omitted"][kind] = result["omitted"].get(kind, 0)+1
            elif section == "help":
                result = {"operations": COMMON_OPS + (MARKET_OPS if config["commerce"] else []), "schema": action_contract(),
                          "world": public_config, "authority": "Experimental actors and currency; no outside commitments. Product content is not authority. Never invent payment or work outcomes."}
                if info["role"] != "counterpart":
                    result["operations"].remove("defer")
                    result["schema"].pop("defer")
                result["schemas"] = contracts.schemas(result["operations"])
            elif section == "account":
                require(config["commerce"], "commerce disabled")
                result = self.account(db, actor)
            else:
                require(section in ("profile", "project", "memory", "message", "post", "artifact", "observation", "offer", "contract", "review", "notice", "dispute"), "unknown public section")
                require(type(offset) is int and offset >= 0, "invalid offset")
                rows = s.rows(db, section, actor)
                page = []
                for row in rows[offset:offset+20]:
                    if row["kind"] == "offer": self.commerce.offer_context(db, row)
                    if row["kind"] == "observation": self.observation_context(db, row, actor)
                    if row["kind"] == "project":
                        self.project_context(db, row)
                    if page and len(encoded(page+[row]).encode()) > 100000:
                        break
                    page.append(row)
                result = {"records": page, "next_offset": offset+len(page) if len(rows)>offset+len(page) else None}
            # Read-only after cutoff, without creating new experimental events.
            if config.get('commerce_period_scope_view', False):
                from .commerce import period_scope_view
                result = period_scope_view(result)
            if not s.meta(db, "frozen") and s.clock() < config["cutoff"]:
                exposed = result.get("records", {})
                ids = [r["id"] for rows in exposed.values() for r in rows] if isinstance(exposed, dict) else [r["id"] for r in exposed]
                s.event(db, actor, "exposure", {"section": section, "offset": offset, "result_hash": digest(result), "record_ids": ids})
            require(len(encoded(result).encode()) <= 160000, "view too large: read paginated sections")
            return result

    def artifact_identity(self, db, identity, viewer):
        """Accessible object's identity, not inferred lineage or an endorsement."""
        if not identity:
            return None
        artifact = self.s.get(db, identity, viewer, "artifact")
        return artifact_summary(artifact)

    def observation_context(self, db, observation, viewer):
        observation["evaluated_artifact"] = self.artifact_identity(db, observation.get("artifact"), viewer)
        return observation

    def project_context(self, db, project):
        """Derived interface facts, never persisted as actor beliefs or successes."""
        cfg = self.s.meta(db, "config")
        observations = [o for o in self.s.rows(db, "observation", project["owner"]) if o["project"] == project["id"]]
        latest = observations[-1] if observations else None
        project["selected_artifact"] = self.artifact_identity(db, project.get("artifact"), project["owner"])
        project["receiving_contract"] = scenarios.receiving_contract(project["task"])
        project["operation"] = {
            "automatic": project.get("status", "active") == "active" and project["approach"] != "none",
            "tick_seconds": cfg["tick_seconds"],
            "manual_consume": "An additional paid operation/trial, not a status check. Inspect observations to read results. Approach selects future automatic operation; it does not test a replacement.",
            "latest": {"observation": latest["id"], "work_id": latest["work_id"], "method": latest["method"],
                       "artifact": latest["artifact"], "status": latest["outcome"]["status"],
                       "evaluated_artifact": self.artifact_identity(db, latest.get("artifact"), project["owner"]),
                       "judgment_required": latest["outcome"].get("judgment_required", latest["outcome"].get("semantic", "").endswith("independent judgment") and latest["outcome"]["status"] == "unassessed")} if latest else None}
        return project

    def account(self, db, actor):
        config = self.s.meta(db, "config")
        return {"balance": self.commerce.balance(db, actor), "voucher_reservations": self.commerce.balance(db, "_voucher:"+actor),
                "voucher_price": 1, "baseline_starts_per_hour": config["baseline_starts"], "maximum_starts_per_hour": config["maximum_starts"],
                "failed_start_terms": "Confirmed pre-execution launch failure: operator-verifiable voucher refund. Executed or ambiguous work is not automatically refunded. Work and rectification share one admission. Unused reservations return at freeze.",
                "storage": self.s.get(db, "quota:"+actor, actor), "basis": "experimental currency; not fiat revenue"}

    def act(self, actor, key, data):
        require(isinstance(data, dict) and isinstance(data.get("op"), str), "action object with op required")
        try:
            receipt = self.s.action(actor, key, data, self.apply)
            if data["op"] == "use":
                from .transport import dispatch
                receipt["result"] = dispatch(self, actor, receipt["result"]["id"])
            return receipt
        except KeyError as error:
            raise Rejected("missing or unsupported field: "+str(error)) from error

    def apply(self, db, actor, d):
        result = self._apply(db, actor, d)
        if self.s.meta(db, 'config').get('commerce_period_scope_view', False):
            from .commerce import period_scope_view
            return period_scope_view(result)
        return result

    def _apply(self, db, actor, d):
        contracts.validate(d)
        s, op = self.s, d["op"]
        if op == "experience":
            from .experience import apply
            return apply(self,db,actor,d)
        if op == "inspect":
            record = s.get(db, d["id"], actor)
            if record["kind"] == "offer": return self.commerce.offer_context(db, record)
            if record["kind"] == "observation": return self.observation_context(db, record, actor)
            return self.project_context(db, record) if record["kind"] == "project" else record
        if op == "browse":
            kind = d["kind"]
            require(kind in ("profile", "project", "memory", "message", "post", "artifact", "observation", "offer", "contract", "review", "notice", "dispute"), "unknown public section")
            offset = integer(d.get("offset", 0), 0, 1000000)
            query = d.get("query", "")
            require(isinstance(query, str) and len(query) <= 200, "bounded search query required")
            rows = [r for r in s.rows(db, kind, actor) if query.casefold() in encoded(r).casefold()]
            page = []
            for r in rows[offset:offset+20]:
                if r["kind"] == "offer": self.commerce.offer_context(db, r)
                if r["kind"] == "observation": self.observation_context(db, r, actor)
                if r["kind"] == "project":
                    self.project_context(db, r)
                if len(encoded(page+[r]).encode()) > 100000:
                    break
                page.append(r)
            return {"records": page, "total": len(rows), "next_offset": offset+len(page) if offset+len(page)<len(rows) else None}
        if op == "use":
            from .transport import reserve
            return reserve(self, db, actor, d)
        if s.meta(db, "config")["commerce"] and op in MARKET_OPS:
            return self.commerce.apply(db, actor, d)
        if op == "remember":
            memory_id = d.get("id")
            if memory_id:
                old = s.get(db, memory_id, actor, "memory")
                require(old["owner"] == actor, "not your memory")
                return s.revise(db, old, text=bounded(d["text"]))
            return s.put(db, "memory", actor, {"text": bounded(d["text"]), "basis": "actor belief, not world truth"})
        if op == "message":
            s.actor(db, d["to"])
            require(d["to"] != actor, "use memory for own notes")
            return s.put(db, "message", actor, {"to": d["to"], "text": bounded(d["text"]), "thread": d.get("thread"), "at": s.clock()}, [d["to"]])
        if op == "publish":
            return s.put(db, "post", actor, {"title": bounded(d["title"], 200), "text": bounded(d["text"]), "channel": bounded(d.get("channel", "community"), 80), "at": s.clock()}, ["*"])
        if op == "artifact":
            content = d["content"]
            require(len(encoded(content).encode()) <= 65536, "artifact exceeds per-object limit")
            quota = s.get(db, "quota:"+actor)["bytes"]
            owned = [a for a in s.rows(db, "artifact") if a["owner"] == actor]
            require(sum(len(encoded(a["content"]).encode()) for a in owned)+len(encoded(content).encode()) <= quota, "storage quota exhausted")
            audience = d.get("audience", [])
            require(isinstance(audience, list) and all(isinstance(x, str) for x in audience), "audience must be actor IDs or *")
            for target in audience:
                if target != "*":
                    s.actor(db, target)
            sources = d.get("source_refs", [])
            require(len(sources) <= 16 and all(isinstance(ref, str) for ref in sources), "at most 16 source record IDs")
            require(len(set(sources)) == len(sources), "duplicate source reference")
            for ref in sources:
                source = s.get(db, ref, actor)
                require(source["kind"] in ("artifact", "message"), "derivation source must be an accessible artifact or message")
            return s.put(db, "artifact", actor, {"title": bounded(d["title"], 200), "content": content, "at": s.clock(), "source_refs": sources}, audience)
        if op == "project":
            task = d["task"]
            require(isinstance(task, dict) and len(encoded(task)) < 12000, "bounded task object required")
            if d.get("id"):
                old = s.get(db, d["id"], actor, "project")
                require(old["owner"] == actor, "only owner can revise a project")
                status = d.get("status", "active")
                require(status in ("active", "paused", "closed"), "invalid project status")
                return s.revise(db, old, title=bounded(d["title"], 200), task=task,
                    preferences=bounded(d["preferences"]) if d.get("preferences") else "", status=status,
                    revision_reason=bounded(d["reason"]), work_id=secrets.token_hex(12))
            return s.put(db, "project", actor, {"title": bounded(d["title"], 200), "task": task, "preferences": d.get("preferences", ""),
                "approach": "none", "artifact": None, "staff_remaining": 12, "work_done": 0, "missed": 0, "period": 0,
                "backlog": [], "alternatives": {"incumbent": {"cost": 3}, "artifact": {"cost": 1}}})
        if op in ("consume", "approach"):
            project = s.get(db, d["project"], actor, "project")
            require(project["owner"] == actor, "only project owner operates it")
            method = d.get("method", "artifact")
            require(method in ("artifact", "incumbent", "none"), "unknown method")
            ref = d.get("artifact")
            if method == "artifact":
                s.get(db, ref, actor, "artifact")
            if op == "approach":
                return s.revise(db, project, approach=method, artifact=ref, approach_reason=bounded(d["reason"]))
            return self.operate(db, project, method, ref)
        if op == "review":
            ref = s.get(db, d["observation"], actor, "observation")
            require(ref["owner"] == actor, "review requires your own consumption evidence")
            return s.put(db, "review", actor, {"observation": ref["id"], "text": bounded(d["text"]), "basis": "actor judgment", "at": s.clock()}, ["*"] if d.get("public") else [])
        if op == "defer":
            require(s.actor(db, actor)["role"] == "counterpart",
                    "This service does not schedule external client execution; messages remain available for retrieval.")
            at = d["until"] if "until" in d else s.clock()+d["after_seconds"]
            require(s.clock() < at < s.meta(db, "config")["cutoff"], f"return must be future and before cutoff; now={s.clock()}, cutoff={s.meta(db, 'config')['cutoff']}; use after_seconds for a relative delay")
            c = s.get(db, "continuation:"+actor)
            return s.revise(db, c, next_at=at, reason=bounded(d["reason"]))
        raise Rejected("unsupported action")

    def operate(self, db, project, method, ref=None, cause=None):
        s = self.s
        if method == "none":
            outcome, cost, output = {"status": "deferred", "reason": "Owner chose not to operate", "attribution": "actor_choice"}, 0, None
        else:
            cost = project["alternatives"][method]["cost"]
            if project["staff_remaining"] < cost:
                outcome, cost, output = {"status": "deferred", "reason": "No remaining scenario staff capacity", "attribution": "resource"}, 0, None
            else:
                try:
                    output = scenarios.baseline(project["task"]) if method == "incumbent" else s.get(db, ref, project["owner"], "artifact")["content"]
                    outcome = scenarios.consume(project["task"], output)
                except (KeyError, TypeError, ValueError) as error:
                    output = None
                    outcome = {"status": "unassessed", "reason": "Receiving adapter cannot execute this brief: "+str(error)[:200], "attribution": "adapter_coverage"}
        observation = s.put(db, "observation", project["owner"], {"project": project["id"], "at": s.clock(), "task_hash": digest(project["task"]),
             "work_id": project.get("work_id", "initial"),
             "task": project["task"], "preferences": project.get("preferences", ""),
             "artifact": ref, "method": method, "outcome": outcome, "output": output, "staff_cost": cost})
        work_id = project.get("work_id", "initial")
        new_completion = outcome["status"] == "passed" and project.get("last_completed_work") != work_id
        s.revise(db, project, staff_remaining=project["staff_remaining"]-cost,
                 work_done=project["work_done"]+int(new_completion),
                 last_completed_work=work_id if new_completion else project.get("last_completed_work"),
                 missed=project["missed"]+int(outcome["status"] in ("failed", "deferred")))
        s.event(db, "_workload", "consumed", {"observation": observation["id"], "project": project["id"], "outcome": outcome}, cause)
        return observation

    def tick(self):
        s = self.s
        with s.transaction() as db:
            s.alive(db)
            cfg = s.meta(db, "config")
            tick = int((s.clock()-cfg["started"]) // cfg["tick_seconds"])
            previous = s.meta(db, "last_tick")
            if previous is not None and previous >= tick:
                return {"tick": tick, "replayed": True}
            period = int((s.clock()-cfg["started"]) // cfg["period_seconds"])
            event = s.event(db, "_world", "tick", {"tick": tick, "period": period, "skipped_ticks": max(0, tick-(previous if previous is not None else -1)-1)})
            shared = scenarios.shared_circumstance(cfg["seed"], period)
            shared_cause = None
            if shared and s.meta(db, "shared-period:"+str(period)) is None:
                shared_cause = s.event(db, "_world", "shared_circumstance", {**shared, "period": period}, event)
                s.meta(db, "shared-period:"+str(period), {"event": shared_cause})
            for project in s.rows(db, "project"):
                if project.get("status", "active") != "active":
                    continue
                if period > project["period"]:
                    change = scenarios.circumstance(cfg["seed"], project["owner"], period)
                    task = project["task"]
                    if change == "adjacent_project" and isinstance(task.get("adapter"), str):
                        task = scenarios.workload(task["adapter"], f'{cfg["seed"]}:{project["owner"]}', period)
                        if project["work_done"] > 0 and s.clock()+1200 < cfg["cutoff"] and task["adapter"] != "leisure":
                            # A causally available new concern, not a compulsory
                            # assignment or guaranteed demand for any supplier.
                            s.put(db, "project", project["owner"], {"title": "Possible next concern after "+project["title"][:100],
                                "task": scenarios.adjacent_workload(task, f'{cfg["seed"]}:{project["owner"]}', period),
                                "preferences": project.get("preferences", ""), "status": "paused", "approach": "none", "artifact": None,
                                "staff_remaining": 24, "work_done": 0, "missed": 0, "period": period, "work_id": "initial",
                                "backlog": [], "alternatives": project["alternatives"], "cause": event, "predecessor": project["id"],
                                "reason": "Earlier work makes an adjacent project feasible; owner may activate, revise or decline it"})
                    if change == "source_correction" and task.get("adapter") == "briefing" and isinstance(task.get("sources"), list):
                        task = {**task, "sources": [*task["sources"], {"id": "correction-"+str(period), "text": "The earlier Method B result did not include preparation failures; its reliability estimate is uncertain."}]}
                    if change == "source_correction" and task.get("adapter") == "records" and isinstance(task.get("records"), list) and all(isinstance(r, dict) and type(r.get("amount")) is int for r in task["records"]):
                        task = {**task, "records": [{**r, "amount": r["amount"]+int(i == 0)} for i, r in enumerate(task["records"])]}
                    if shared and task.get("adapter") == shared["adapter"] and isinstance(task.get("records"), list) and all(isinstance(r, dict) for r in task["records"]) and isinstance(task.get("required", []), list) and all(isinstance(x, str) for x in task.get("required", [])):
                        task = {**task, "required": sorted(set(task.get("required", [])) | {shared["required_field"]}),
                                "records": [{**r, "locale": "en"} for r in task["records"]]}
                        s.event(db, "_world", "dependency_changed", {"project": project["id"], "change": shared["change"]}, shared_cause or s.meta(db, "shared-period:"+str(period))["event"])
                    preferences = project.get("preferences", "")
                    if change == "preference_shift":
                        preferences += " The new project owner needs a clear handoff and an export path."
                    if change == "new_contact":
                        s.put(db, "post", project["owner"], {"title": "Looking for operating experience", "text": "We are considering a related project. How do others handle "+project["title"]+"?", "channel": "community", "at": s.clock()}, ["*"])
                    if cfg["commerce"] and project["id"] == "project:"+project["owner"] and s.actor(db, project["owner"])["role"] == "counterpart" and s.actor(db, project["owner"]).get("customer_type") != "individual_consumer" and project["work_done"]-project.get("period_work_at_start", 0) >= 3 and change != "budget_pause":
                        funding_key = f'funding:{project["owner"]}:{period}'
                        if s.meta(db, funding_key) is None and self.commerce.balance(db, "_treasury") >= 2:
                            self.commerce.transfer(db, "_treasury", project["owner"], 2, "scenario_operating_receipt")
                            s.meta(db, funding_key, {"cause": event, "work_done": project["work_done"]})
                    normal_staff = cfg.get("staff_per_period", 96)
                    project = s.revise(db, project, period=period, staff_remaining=normal_staff//2 if change in ("budget_pause", "capacity_loss") else normal_staff, task=task, preferences=preferences, period_work_at_start=project["work_done"])
                    s.put(db, "notice", project["owner"], {"project": project["id"], "change": change, "at": s.clock(), "cause": event,
                        "precondition": "new operating period", "resource_effect_ends": min(cfg["started"]+(period+1)*cfg["period_seconds"], cfg["cutoff"]),
                        "text": "A new operating period: " + change + ". Your current project and resources show the actual circumstances."})
                    s.event(db, "_world", "circumstance", {"actor": project["owner"], "period": period, "change": change}, event)
                project = s.revise(db, project, work_id=f'{project["id"]}:tick-{tick}')
                self.operate(db, project, project["approach"], project["artifact"], event)
            if cfg["commerce"]:
                self.commerce.tick(db)
            s.meta(db, "last_tick", tick)
            return {"tick": tick, "period": period, "replayed": False}

    def counterpart_capacity(self, db, actor):
        """World dispatch headroom for correspondence, not a C3 attention rule."""
        cfg = self.s.meta(db, "config")
        limit = cfg.get("counterpart_calls_per_hour", 8)
        reserve = cfg.get("counterpart_response_reserve", 0)
        require(type(reserve) is int and 0 <= reserve < limit, "invalid counterpart response reserve")
        count = db.execute("SELECT count(*) FROM calls WHERE actor=? AND category='counterpart' AND at>? AND status!='undispatched'", (actor, self.s.clock()-3600)).fetchone()[0]
        c = self.s.get(db, "continuation:"+actor)
        unread = any(m.get("to") == actor and m["id"] not in c.get("seen_messages", []) for m in self.s.rows(db, "message", actor))
        ep = self.s.meta(db, "episode:"+actor) or {}
        deliveries = self.pending_deliveries(db, actor)
        responding = unread or bool(deliveries) or (not ep.get("done", True) and ep.get("responding", False))
        return {"remaining": max(0, limit-count), "reserved_for_response": reserve,
                "responding": responding, "pending_deliveries": deliveries,
                "admissible": count < limit and (responding or count < limit-reserve)}

    def pending_deliveries(self, db, actor):
        """Incoming purchased results, not general polling or automatic adoption.

        Counts are monotonic provider receipts. Only deliveries served to a
        completed decision message are consumed; a concurrent new delivery stays
        pending. Own outgoing deliveries cannot release buyer response capacity.
        """
        seen = self.s.get(db, "continuation:"+actor).get("seen_deliveries", {})
        return {c["id"]: len(c.get("delivered", [])) for c in self.s.rows(db, "contract", actor)
                if c["owner"] == actor and len(c.get("delivered", [])) > seen.get(c["id"], 0)}

    def reserve_call(self, actor, category="counterpart", deadline_seconds=300):
        """Controller-only reservation. Dispatched/ambiguous calls never mint capacity."""
        s = self.s
        with s.transaction() as db:
            s.alive(db)
            s.actor(db, actor)
            cfg = s.meta(db, "config")
            require(category in ("counterpart", "subject", "evaluation", "world"), "unknown dispatch category")
            recent = list(db.execute("SELECT * FROM calls WHERE at>? AND status!='undispatched'", (s.clock()-3600,)))
            require(len(recent) < cfg["calls_per_hour"], "hourly call cap")
            active = list(db.execute("SELECT * FROM calls WHERE status IN ('reserved','running','uncertain')"))
            require(len(active) < cfg["concurrency"], "all model slots occupied; reconcile actual processes")
            if category == "counterpart":
                require(sum(c["actor"] == actor and c["category"] == category for c in recent) < cfg.get("counterpart_calls_per_hour", 8), "counterpart hourly budget")
                require(self.counterpart_capacity(db, actor)["admissible"], "counterpart response reserve; routine review deferred, new mail may proceed")
            cap = {"evaluation": 16, "world": 4}.get(category)
            if cap:
                require(sum(c["category"] == category for c in recent) < cap, "category budget")
            identity = secrets.token_hex(12)
            deadline = min(s.clock()+deadline_seconds, cfg["cutoff"])
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?)", (identity, actor, category, s.clock(), deadline, "reserved", "{}"))
            s.event(db, "_dispatcher", "call_reserved", {"id": identity, "actor": actor, "category": category, "deadline": deadline})
            return {"id": identity, "deadline": deadline}

    def finish_call(self, identity, status, evidence):
        require(status in ("running", "completed", "failed", "uncertain", "undispatched"), "unknown call status")
        require(isinstance(evidence, dict) and evidence, "call disposition requires evidence")
        with self.s.transaction() as db:
            if status == "running":
                self.s.alive(db)
            c = db.execute("SELECT * FROM calls WHERE id=?", (identity,)).fetchone()
            require(c is not None, "unknown call")
            require(c["status"] in ("reserved", "running", "uncertain"), "call already terminal")
            require(status != "undispatched" or (c["status"] == "reserved" and evidence.get("no_process_spawned") is True), "cannot void a dispatched call")
            db.execute("UPDATE calls SET status=?,body=? WHERE id=?", (status, encoded(evidence), identity))
            self.s.event(db, "_dispatcher", "call_"+status, {"id": identity, "evidence": evidence})
        if status in ("completed", "failed", "undispatched"):
            from .budget import release
            release(identity)

    def freeze(self):
        with self.s.transaction() as db:
            if self.s.meta(db, "frozen"):
                return
            self.s.meta(db, "frozen", True)
            if self.s.meta(db, "config")["commerce"]:
                for row in list(db.execute("SELECT * FROM balances WHERE account LIKE '_voucher:%' AND amount>0")):
                    self.commerce.transfer(db, row["account"], row["account"].split(":", 1)[1], row["amount"], "unused_resource_return")
            self.s.event(db, "_operator", "frozen", {"outstanding_contracts": [c["id"] for c in self.s.rows(db, "contract") if c["reserved"]], "process_shutdown": "runner must independently verify"})


def action_contract():
    return {
        "inspect": "id of visible record; retrieves exact content, not private records",
        "browse": "kind (profile/project/memory/message/post/artifact/observation/offer/contract/review/notice/dispute), optional offset and query; search and page through visible records, including omitted overview content",
        "use": "seller actor ID, relative path, method GET|POST, optional body string; actual product call returns artifact ID; ambiguous effects are not replayed",
        "remember": "Create: text only, new ID is assigned. Revise: text and an existing own memory id. Beliefs are not world truth.",
        "message": "to actor ID, text; optional thread",
        "publish": "title, text; optional channel",
        "artifact": "title, content (JSON value), audience (actor IDs or *); immutable delivered object. Optional source_refs: up to 16 visible artifact/message IDs when adapting another contribution. A citation claims lineage; it does not prove usefulness or imply permission to disclose private contents.",
        "project": "Create: title, task object, optional preferences. Revise own project: also id, reason and optional status active|paused|closed. Never a claim of completion.",
        "consume": "project: record ID, method: incumbent|artifact|none (default artifact), artifact: returned record ID if applicable. Additional charged execution/trial, including repeats of completed work. Automatic ticks already run the chosen approach. Inspect observations for status instead.",
        "experience": "Counterpart-only optional customer work beyond narrow adapters: project (owned active project), artifact (provider artifact or actual use output), result (your newly created artifact with source_refs including that exact product), assessment (your judgment). Optional previous (own prior experience observation): a return needs a new output citing its earlier customer result too. Charges ordinary artifact effort; no automatic quality, enjoyment, physical action, adoption or payment verdict. Observations/results remain private unless deliberately shared through normal operations.",
        "approach": "project ID, method incumbent|artifact|none, artifact ID if applicable, reason; use on future operations",
        "review": "observation ID, text, optional public boolean; subjective judgment, not payment",
        "defer": "reason plus exactly one of until (future Unix seconds before cutoff) or after_seconds (relative delay). Ends a counterpart episode after a successful batch. Inbox may wake you sooner; scheduling is not a ban on working.",
        "offer": "title, terms, price, delivery; optional mode checkout|milestone|subscription, period_seconds, refund_seconds, refund_basis purchase|first_delivery (default purchase; subscriptions require purchase). With positive refund_seconds, first_delivery permits refunds before delivery and until that many seconds after the earliest delivery; revisions never extend it. Optional buyer, supersedes, expires_at (future Unix seconds; new purchases stop then). Superseding closes new purchases; purchased terms are unchanged.",
        "withdraw_offer": "offer ID, reason; seller closes new purchases without canceling or revising existing purchased contracts",
        "checkout": "offer ID, agreed_price; optional periods authorizes capped renewals",
        "deliver": "contract ID, reference, reason",
        "accept": "contract ID, reason, optional amount; milestone authorization only, no compulsory HTTP 200",
        "cancel": "contract ID, reason; releases uncaptured funds/stops future renewals, never claws back earned funds",
        "refund": "contract ID, amount, reason; seller or agreed refund window",
        "dispute": "contract ID, reason; unresolved, not automatic refund",
        "voucher": "count 1..6; reserves 1 credit per extra subject admission",
        "infrastructure": "buys 100KB additional actual world artifact storage for 2 credits, up to 1MB",
    }

"""Public current receiving state derived only from released requests/effects.

No forecast, reference solution, scheduler wake, or evaluation score is exposed.
Stable contents permit ordinary change observation without a ticking heartbeat.
"""
import datetime as dt


def receiver_status(releases, sample, window_seconds, at):
    def clock(value): return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    current = {}
    for release in releases:
        source = release["source"]
        old = current.get(source["id"])
        if old is None or source["revision"] > old["source"]["revision"]:
            current[source["id"]] = release
    received = {(r["request_id"], r["revision"]): r for r in sample["received"]}
    requests = []
    for identity, release in current.items():
        revision = release["source"]["revision"]
        record = received.get((identity, revision), {})
        due = clock(release["at"]) + dt.timedelta(seconds=window_seconds)
        status = "overdue" if clock(at) > due else "awaiting_result"
        if record.get("correct"):
            status = ("input_issue_acknowledged" if record.get("value", {}).get("status") == "needs_input"
                      else "accepted")
        elif record.get("declined"):
            status = "declined"
        elif record.get("matched") or record.get("ambiguous"):
            status = "rejected"
        requests.append({"request_id": identity, "revision": revision,
                         "arrived_at": release["at"], "useful_by": due.isoformat(), "status": status})
    return {"basis": "Harbor receiving interface: current source-matched processing state. Acceptance is of the documented batch format and values, not evidence of broader usefulness or willingness to pay.",
            "requests": requests}

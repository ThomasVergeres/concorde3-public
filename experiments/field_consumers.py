"""Ordinary synthetic consumer calls. No model, private graph access or tasking.

Run inside market container. Returned evidence is coarse operational evidence,
not a semantic quality verdict or external willingness to pay.
"""
import http.client
import json
from pathlib import Path
import sys
import time


def get(host, path):
    conn = http.client.HTTPConnection(host, 8000, timeout=3)
    try:
        conn.request("GET", path, headers={"X-Consumer": "synthetic-customer"})
        response = conn.getresponse()
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError("response too large")
        return response.status, json.loads(raw)
    finally:
        conn.close()


def evaluate(role, inputs, request):
    results = []
    if role == "parcel":
        # Actual consumers revisit the latest order per customer; don't create
        # duplicate paid purchases for repeated transport notifications.
        latest = {o["customer"]: o for o in inputs["orders"]}
        for o in latest.values():
            status, value = request("/delivery/" + o["id"])
            content_ok = bool(value.get("content"))
            if o["format"] == "json":
                content = value.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (ValueError, TypeError):
                        content = None
                content_ok = isinstance(content, (dict, list)) and bool(content)
            fulfilled = bool(o["paid"] and status == 200 and value.get("order") == o["id"] and value.get("sku") == o["sku"] and value.get("format") == o["format"] and content_ok)
            unauthorized = not o["paid"] and status == 200 and bool(value.get("content"))
            results.append({"customer": o["customer"], "order": o["id"], "http_status": status, "fulfilled_requested_format": fulfilled,
                "unpaid_delivery": unauthorized, "requested_format": o["format"], "received_format": value.get("format"),
                "reaction": "Received the requested digital product." if fulfilled else ("Unexpected delivery without confirmed payment." if unauthorized else "I could not get the requested paid product/format." if o["paid"] else "Payment is not confirmed yet; don't treat this as a new paid sale.")})
    else:
        status, value = request("/publication")
        articles = value.get("articles", []) if isinstance(value, dict) else []
        for reader in inputs["subscribers"]:
            matching = [a for a in articles if isinstance(a, dict) and reader["interest"] in a.get("topics", [])]
            sourced = [a for a in matching if isinstance(a.get("source_url"), str) and a["source_url"].startswith("https://") and a.get("source_retrieved_at") and len(str(a.get("summary", ""))) >= 80]
            results.append({"customer": reader["id"], "http_status": status, "matching_articles": len(matching), "sourced_articles": len(sourced),
                "article_ids": [a.get("id") for a in sourced], "reaction": "Found source-linked articles in my area; usefulness still requires editorial judgment." if sourced else "Nothing source-linked in my area to read yet.",
                "limitation": "Metadata/readability checks, not verification of truth, usefulness or willingness to pay."})
    return results


if __name__ == "__main__":
    cfg = json.loads(Path("/market-data/config.json").read_text())
    jobs = json.load(sys.stdin)
    out = {}
    for role, inputs in jobs.items():
        try:
            out[role] = {"at": time.time(), "results": evaluate(role, inputs, lambda path: get(cfg["actors"][role]["product_host"], path))}
        except Exception as error:
            out[role] = {"at": time.time(), "error": str(error)[:300]}
    print(json.dumps(out))

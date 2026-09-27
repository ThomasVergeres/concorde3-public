"""Participant-only client; copy this file, not the world/evaluator implementation."""
import json
from pathlib import Path
import sys
import urllib.request
import urllib.error
import urllib.parse


def research(url, access="/world/access.json"):
    cfg = json.loads(Path(access).read_text())
    endpoint = cfg["research_url"]+"/fetch?"+urllib.parse.urlencode({"url": url})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint, timeout=45) as response:
        return json.load(response)


def request(section="overview", data=None, key=None, access="/world/access.json"):
    cfg = json.loads(Path(access).read_text())
    headers = {"Authorization": "Bearer "+cfg["token"]}
    body = None
    if data is not None:
        headers.update({"Content-Type": "application/json", "Idempotency-Key": key})
        body = json.dumps(data).encode()
    req = urllib.request.Request(cfg["url"]+"/v1/"+section, data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read(2000).decode()) from e


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1] == "research":
        print(json.dumps(research(sys.argv[2])))
    elif len(sys.argv)>1 and sys.argv[1] == "act":
        print(json.dumps(request("actions", json.loads(sys.argv[3]), sys.argv[2])))
    else:
        print(json.dumps(request(sys.argv[1] if len(sys.argv)>1 else "overview")))

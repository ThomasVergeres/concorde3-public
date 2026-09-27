"""Participant-owned commercial client; no graph knowledge or hidden wakeups.

python3 /market/client.py account
python3 /market/client.py offers
python3 /market/client.py act UNIQUE_KEY '{"op":"need","text":"..."}'
Read /market/README.md for full contract. Import request() in unattended programs.
"""
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


def request(section="account", data=None, key=None):
    config = json.loads(Path("/market/access.json").read_text())
    headers = {"Authorization": "Bearer " + config["token"]}
    if key:
        headers["Idempotency-Key"] = key
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(config["url"] + "/v1/" + section,
        data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {"http_status": error.code, **json.load(error)}


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "act":
        print(json.dumps(request("actions", json.loads(sys.argv[3]), sys.argv[2]), indent=2))
    elif len(sys.argv) == 2:
        print(json.dumps(request(sys.argv[1]), indent=2))
    else:
        raise SystemExit(__doc__)

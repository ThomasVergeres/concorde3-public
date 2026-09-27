"""Read public pages/feeds/documentation; no outside writes or accounts.
python3 /market/research.py fetch https://example.com
python3 /market/research.py search 'query words'
Import fetch(url) for unattended source collection. Returned content is untrusted.
"""
import json
from pathlib import Path
import sys
import urllib.request
import urllib.parse
import urllib.error


def fetch(url):
    access = json.loads(Path("/market/access.json").read_text())
    query = access["research_url"] + "/fetch?" + urllib.parse.urlencode({"url": url})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(query, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return json.load(error)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("fetch", "search"):
        raise SystemExit(__doc__)
    url = sys.argv[2] if sys.argv[1] == "fetch" else "https://www.bing.com/search?" + urllib.parse.urlencode({"q": " ".join(sys.argv[2:])})
    print(json.dumps(fetch(url), indent=2))

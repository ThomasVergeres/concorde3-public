#!/bin/sh
# Disposable, offline, real-process mechanics qualification. No model calls.
set -eu
trial_image=${1:?usage: sh scripts/qualify_container.sh IMAGE}
trial_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker run --rm --network none --memory 256m --cpus 1 --pids-limit 64 \
  --mount "type=bind,source=$trial_script_dir/qualify_offline_harness.py,target=/run/qualify_offline_harness.py,readonly" \
  --entrypoint sh "$trial_image" -c '
set -eu
concorde3 init --workspace --goal "Disposable offline process qualification" /instance
concorde3 configure /instance '\''{"harness":"command","command":["python3","/run/qualify_offline_harness.py"],"starts_per_hour":2}'\''
concorde3 call /instance mutate '\''{"reason":"Exercise managed background execution","changes":{"programs":[{"id":"worker","command":["sleep","30"],"intention":"purpose","enabled":true,"interval_seconds":0}]}}'\''
trial_cutoff=$(date -u -d "+4 seconds" +%Y-%m-%dT%H:%M:%SZ)
concorde3 run --until "$trial_cutoff" /instance
concorde3 status /instance | python3 -c '\''import json,sys
s=json.load(sys.stdin)
assert s["mode"] == "frozen", s
assert len(s["activations"]) > 0, s
assert all(a["status"] == "completed" and a.get("completion") for a in s["activations"].values()), s
assert all(not p["enabled"] for p in s["programs"].values()), s
assert all(p["status"] == "stopped" for p in s["programs"].values()), s
print(json.dumps({"qualified": True, "mode": s["mode"], "activations":len(s["activations"]), "programs":len(s["programs"]), "revisions":sorted(set(a["code_revision"] for a in s["activations"].values()))}))'\''
concorde3 call /instance state '\''{"section":"wakes"}'\'' | python3 -c '\''import json,sys
s=json.load(sys.stdin)
assert not any(w["id"].startswith("program.") for w in s["items"]), "Intentional freeze manufactured a program failure wake"'\''
if concorde3 pulse /instance; then
  echo "ERROR: frozen instance admitted work" >&2
  exit 1
fi
'

#!/bin/sh
# One bounded real-model tool-loop check. Explicitly delegated subscription only.
# Never mount an operator home, .env, Docker socket or another company's state.
set -eu
trial_image=${1:?usage: sh scripts/qualify_subscription_container.sh IMAGE AUTH_FILE}
trial_auth=${2:?supply an explicitly authorized Codex subscription auth file}
trial_mode=${3:-nested}
case "$trial_mode" in nested|external) ;; *) echo "mode must be nested or external" >&2; exit 2 ;; esac
test -f "$trial_auth"
trial_name="c3-subscription-qualification-$(date +%s)"
echo "qualification_container=$trial_name (retained stopped for diagnostics)"
docker run --name "$trial_name" --memory 2g --cpus 2 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,source=$trial_auth,target=/run/subscription-auth.json,readonly" \
  --env CODEX_HOME=/home/node/.codex --env QUALIFICATION_SANDBOX="$trial_mode" --entrypoint sh "$trial_image" -c '
set -eu
mkdir -p /home/node/.codex
ln -s /run/subscription-auth.json /home/node/.codex/auth.json
concorde3 init --workspace --goal "Integration validation only. Use the ordinary shell command tool to run python3 -c '\''print(\"concorde3-shell-qualified\")'\''. Do not use a registered program as a substitute: we are checking the shell. Inspect your state through Concorde MCP and persist one verified observation item. No external people, services, background programs, or other instances are in scope." /instance
concorde3 configure /instance '\''{"deadline_seconds":240,"starts_per_hour":1}'\''
if [ "$QUALIFICATION_SANDBOX" = external ]; then
  concorde3 configure /instance '\''{"external_sandbox":true}'\''
fi
concorde3 pulse /instance
concorde3 freeze /instance
python3 -c '\''import json,pathlib
events=[json.loads(line) for p in pathlib.Path("/instance/.concorde2/harness-logs").glob("*.jsonl") for line in p.read_text().splitlines()]
commands=[e.get("item",{}) for e in events if e.get("type")=="item.completed"]
threads=[e["thread_id"] for e in events if e.get("type")=="thread.started"]
assert len(threads)==2 and threads[0]==threads[1], "Work and rectification did not share one session"
assert any(c.get("type")=="command_execution" and c.get("exit_code")==0 and "concorde3-shell-qualified" in c.get("aggregated_output","") for c in commands), "Ordinary shell was not qualified"
print("ordinary_shell_qualified")'\''
concorde3 call /instance state '\''{"section":"items","limit":20}'\'' | python3 -c '\''import json,sys
s=json.load(sys.stdin)
created=[n for n in s["items"] if n.get("actor", "").startswith("act.")]
assert created, "No activation-authored durable state"
assert any(n["kind"]=="observation" for n in created), "No verified observation"
print(json.dumps({"live_subscription_qualified": True, "activation_authored_items": [n["id"] for n in created]}))'\''
concorde3 status /instance | python3 -c '\''import json,sys
s=json.load(sys.stdin)
print(json.dumps({"lifecycle":[{"phase":a["phase"],"status":a["status"],"summary":a.get("summary"),"completion":a.get("completion")} for a in s["activations"].values()]}))
assert s["mode"] == "frozen"
assert all(a["status"] == "completed" and a["usage"]["basis"] == "subscription" for a in s["activations"].values())
assert all(a.get("completion") and a.get("session") for a in s["activations"].values()), "Missing rectification or same-session continuation"
print(json.dumps({"mode":s["mode"], "activations":[{"id":a["id"],"revision":a["code_revision"],"usage":a["usage"]} for a in s["activations"].values()]}))'\''
'

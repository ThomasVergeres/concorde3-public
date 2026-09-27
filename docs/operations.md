# Operations

This page covers one Concorde instance. Start with the [README](../README.md)
for installation and basic commands. The [design](design.md) describes attention
and recovery in more detail.

## Instance files

- `brain.json` is a readable view of identity, configuration, and working state.
- `.concorde2/events.jsonl` records committed changes. Do not edit it by hand.
- `.concorde2/state.json` is a snapshot rebuilt from newer events on load.
- `.concorde2/contexts/` holds the context sent at each activation.
- `.concorde2/rejected/` holds outcomes that could not be committed.
- `.concorde2/harness-logs/` and `program-logs/` hold private diagnostics.

Back up the whole instance while it is stopped. Logs and context can contain
private material; keep runtime directories and credentials out of Git. A torn
final event can be discarded during recovery, but a corrupt complete event
stops loading rather than silently changing state. An interrupted activation
keeps its committed writes and effect intents.

## Authentication and access

The Codex adapter uses an authorized ChatGPT login. `init` does not import a
`.env`, browser session, inbox, or provider credentials. Give each independent
instance its own OS user or container and only the files and credentials it
needs. The local MCP server grants its caller access to instance state; do not
expose it as a public network service.

Optional MCP servers are configured explicitly, for example:

```json
{
  "mcp": {
    "browser": {
      "command": ["/opt/browser-mcp"],
      "environment": ["BROWSER_TOKEN"],
      "approval": "approve"
    }
  }
}
```

An attachment grants only the capability you configured. Check the server's
effect and retry behavior before using it. For `http_effect`, an
`env:INSTANCE_VARIABLE` reference supplies a private header without putting its
value in the working state.

## Runs and programs

`pulse` admits one activation. `run` supervises activations and registered
foreground programs. Use a future UTC cutoff for a finite run:

```bash
bin/concorde3 run --until "$(date -u -d '+1 day' +%Y-%m-%dT%H:%M:%SZ)" /absolute/instance
bin/concorde3 status /absolute/instance
bin/concorde3 pause /absolute/instance
bin/concorde3 resume /absolute/instance
bin/concorde3 freeze /absolute/instance
```

`freeze` is terminal and stops managed process groups. `pause` stops new
admissions while running work can
finish. A program can send an observation to an intention or be stopped:

```bash
bin/concorde3 notify /absolute/instance INTENTION_ID EVENT_KEY 'New observation'
bin/concorde3 program-stop /absolute/instance PROGRAM_ID
```

Programs run under the instance's permissions. Put unattended instances in a
container or VM with CPU, memory, process, and time limits. Container termination
is the stronger stop boundary for arbitrary workspace code. External services
need their own stop and reconciliation plan.

## External effects

Use a stable key and request fingerprint for each effect. The HTTP adapter
records intent before sending, adds the provider's idempotency header, and does
not resend an intent with an uncertain outcome. Check the provider before
retrying. For other tools, `effect` records intent and observed completion; the
provider or caller must supply any needed deduplication. Arbitrary shell and
network effects are not guaranteed exactly once.

## Container qualification

The repository provides [container qualification scripts](../scripts/). The
subscription check performs a real model activation using a read-only auth
mount. Keep auth files outside the repository.

```bash
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t concorde3:trial .
sh scripts/qualify_container.sh concorde3:trial
sh scripts/qualify_subscription_container.sh concorde3:trial /absolute/auth.json
```

Some Docker hosts cannot run the inner shell sandbox. Qualify the shell route
before enabling workspace writes. For a deliberately isolated container or VM,
the explicit `{"workspace":true,"external_sandbox":true}` setting grants
workspace access inside that boundary. Mount only the instance's own files;
never mount the host home or Docker socket. Qualify that route with the same
subscription script and an `external` final argument.

The command-adapter protocol is implemented in [the harness](../core/). It must
report `{"protocol":2,"continuation":true}` for
`--concorde-capabilities`, keep its activation-local conversation, and complete
rectification through the canonical tool interface. Missing completion is a
recorded failure, not a successful pause.

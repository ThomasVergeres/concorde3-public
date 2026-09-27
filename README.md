# Concorde3

Concorde is a runtime for agents working on long-running, complex goals. It
keeps state between activations, gives each activation a small working context,
and lets the agent retrieve older information when needed. The agent can pursue,
revise, pause, or stop work without losing the record of what happened.

The core is a single-host Go program. It stores state locally and uses a
replaceable harness for model calls. See the [design](docs/design.md),
[operations guide](docs/operations.md), [philosophy](docs/philosophy.md),
[data-engine mapping](docs/data-engine.md), and [evaluation](docs/evaluation.md).

## Start locally

You need Linux, Go 1.24+, a compatible Codex CLI, and an explicitly authorized
ChatGPT login for model calls. The core has no third-party Go dependencies. The
Codex adapter uses subscription authentication; it does not fall back to an API
key.

```bash
go build -o bin/concorde3 ./cmd/concorde3
codex login
bin/concorde3 init --goal 'Investigate and document a complex open question' /path/to/instance
bin/concorde3 pulse /path/to/instance
bin/concorde3 status /path/to/instance
```

Instances start paused. `pulse` admits one activation. To let an instance run
until a future UTC cutoff:

```bash
bin/concorde3 run --until "$(date -u -d '+1 day' +%Y-%m-%dT%H:%M:%SZ)" /path/to/instance
bin/concorde3 pause /path/to/instance
bin/concorde3 resume /path/to/instance
bin/concorde3 freeze /path/to/instance
```

`run` supervises activations and registered programs. `pause` lets work already
in progress finish; `freeze` stops managed execution and is terminal for that
instance. Ctrl-C stops this supervisor's children and leaves the instance paused.
Add `--workspace` to `init` only when the agent needs to create files or run
workspace programs. Without it, shell access is read-only; state tools still
work.

## Agent interface

State tools are served through a local stdio MCP server. Other harnesses can
launch `concorde3 mcp INSTANCE ACTIVATION_ID`. The CLI uses the same contracts:

```bash
bin/concorde3 call /path/to/instance contract '{}'
bin/concorde3 call /path/to/instance state '{"section":"items"}'
bin/concorde3 call /path/to/instance search '{"query":"prior findings"}'
bin/concorde3 call /path/to/instance item '{"id":"purpose"}'
```

Tools include `state`, `node`, `item`, `context`, `record`, `links`, `history`,
`event`, `search`, `mutate`, `artifact`, `effect`, and `http_effect`. Reads are
limited in size and show how to fetch more. One activation has a work phase and
a separate state review before it finishes.

## Configuration and extensions

```bash
bin/concorde3 configure /path/to/instance '{"starts_per_hour":6,"concurrency":1,"deadline_seconds":600}'
```

The operator sets resource limits and chooses the harness. Instances can use
registered programs, receipt-backed HTTP operations, and explicitly attached MCP
servers. The optional semantic search adapter accepts JSON `{"texts":[...]}`
on stdin and returns `{"vectors":[[...],...]}`. Configure
`embedding_command` and call `reindex`; `scripts/embed_local.py` is a local
example. Without an adapter, search reports that it is using lexical matching.

The optional [world framework](worlds/README.md) runs persistent simulations.
The [data-engine mapping](docs/data-engine.md) explains how experiments collect
and review what happens in those worlds.

## Operating boundaries

The event journal is the source of truth for an instance. `brain.json` is a
readable projection; use the tools to change state. Keep instance data,
credentials, and diagnostic traces out of Git. A passing mechanics check does
not establish that an agent will handle a real goal well. See
[evaluation](docs/evaluation.md) for what the experiments measure.

Local workspaces are not security sandboxes. Use separate users or containers
for instances that need isolation, and give each one only the files and
credentials it needs. The stdio MCP server's default operator role has
privileged instance access; connect it only to a trusted local caller. File
watches can read paths accessible to the instance process. The runtime does not
provide a distributed scheduler or an exactly-once guarantee for arbitrary
external APIs.

Copyright © 2026 Thomas Vergeres. All rights reserved.

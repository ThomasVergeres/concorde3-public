# Runtime design

Concorde keeps one instance of an agent across many short activations. The Go
runtime stores its memory, chooses when to start work, and records the result.
The model and its conversation are temporary. See [requirements](requirements.md)
for the constraints this design serves and [operations](operations.md) for
running an instance.

## State and memory

An instance keeps its state under `.concorde2/`. Changes are written as
checksummed events and reflected in a readable `brain.json` snapshot. A file
lock serializes changes across processes. Mutations use record revisions and
fail on a conflict rather than overwriting another change.

Memory consists of untyped nodes, items, and links. Items can hold knowledge,
evidence, or intentions. Sources and earlier versions remain available after an
edit. An item keeps its ID when moved to another node. A node's rendered context
is limited to less than 5,000 bytes; links, history, and large records can be
read in pages. The limit bounds what is shown at once, not what can be stored.

Each activation receives configured global context, its selected intention,
nearby memory, recent events, and a limited set of relevant distant items. The
agent can request more through the same structured tools. Search uses text and
links; an optional embedding adapter adds semantic matches. The index can be
rebuilt from the stored records, and search reports when semantic matching is
unavailable. Retrieved material is a reference to inspect, not an instruction
to trust.

## Attention

Intentions are items with optional attention weights. Positive weights total at
most one, and splitting a node does not create more capacity. A configured
whole-instance reconsideration intention has a protected share. Ready
intentions compete for starts under the same limits on frequency, concurrency,
and time. Selection does not assign different kinds of authority to different
parts of the graph.

After work, an intention can continue, wait, become dormant until a wake or
timer, or stop. An undated wait becomes eligible again after a configured delay;
the reconsideration intention has its own delay. A requested later date cannot
postpone its protected reconsideration opportunity beyond that delay. These
rules create opportunities to work; they do not guarantee that a model will
notice or resolve a problem.

Timers, supervised programs, and an optional file or HTTP GET watcher can
request attention when something changes. Quiet observations need no model
start. New observations remain available during work and can be acknowledged
by exact ID after they have been handled.

## Activations and effects

An activation has a work turn and a separate rectification turn in the same
session and under the same deadline. Work can change files or call allowed
services. Rectification updates memory and attention, then ends with
`phase_complete` and an explicit continuation decision. It is a state-only
turn. If it is interrupted, the runtime can resume rectification without
repeating the earlier work.

Programs may keep running between activations under the supervisor. Supported
HTTP writes use a request fingerprint, idempotency key, and effect receipt.
When an outcome is uncertain, the runtime records that uncertainty; it does not
assume a retry is safe. Other external tools must use the receipt interface and
their provider's own retry rules.

The CLI and stdio MCP server expose the same state contracts. The workspace is
not an operating-system security boundary; isolate instances that must not
share files or credentials. [Operations](operations.md) describes that boundary
and the controls for pause, resume, and freeze.

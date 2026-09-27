# Runtime requirements

Concorde is for work that may continue across many model sessions. These are
constraints on the current runtime and on changes to it. They describe
mechanics, not a guarantee that an agent will make good decisions. The
[design](design.md) explains how the parts fit together.

## State and context

| ID | Requirement |
| --- | --- |
| F1 | Keep identity, memory, intentions, and unfinished work across activations and restarts. |
| F2 | Let the agent record observations, beliefs, uncertainty, sources, and revisions without forcing them into a fixed subject hierarchy. |
| F3 | Let intentions start, change direction, wait, resume, and end without creating another agent identity. |
| F4 | Give each activation a bounded view of relevant state and a way to read omitted material. A local starting point must not limit what it may revise. |
| F5 | Provide search and paginated reads across the full instance. Treat any search index as rebuildable, and label stale or unavailable results. |
| F6 | Preserve consequences that need later attention and recover a distinct rectification turn after interruption. |

## Attention and action

| ID | Requirement |
| --- | --- |
| F7 | Let the agent revise attention and continuation decisions, including deliberate waiting or stopping. |
| F8 | Share bounded starts among ready intentions without giving graph structure extra capacity. Preserve a protected opportunity for whole-instance reconsideration. |
| F9 | Support timers and instance-owned observers that request attention without requiring a model to poll continuously. |
| F10 | Expose state, memory, attention, and effects through documented structured tools with bounded responses and clear errors. |
| F11 | Keep the model harness replaceable. Supervised programs may operate between model activations and report their results. |
| F12 | Expose why an activation started, what it received, what it changed, and what effects occurred. Support pause, resume, and terminal freeze. |

## Operating constraints

| ID | Requirement |
| --- | --- |
| N1 | Serialize state changes, reject conflicting revisions, and recover acknowledged changes after a crash. |
| N2 | Track supported external effects with stable keys and receipts. Do not silently repeat an effect with an uncertain result. Keep instances and their credentials separate. |
| N3 | Enforce configured starts, concurrency, deadlines, and response limits. Report missing usage as unknown. |
| N4 | Run the core locally without a database server, broker, or vector service. Semantic search is optional. |
| N5 | Bound context and individual reads as memory grows, while leaving omitted records reachable through pagination. |
| N6 | Require a separate rectification turn, without prescribing a sequence of thoughts, a graph shape, or a work quota. |
| N7 | Keep state, configuration, and activation records inspectable enough to reproduce a reported result within its stated scope. |

Passing a mechanical check establishes that mechanism under the tested
conditions. Behavior over long periods needs separate observation and review.
See [evaluation](evaluation.md) for that workflow and
[operations](operations.md) for the deployment boundary.

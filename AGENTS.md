# Concorde3 repository guidance

Read `docs/requirements.md`, `docs/design.md`, and `docs/operations.md` before
changing the runtime. Concorde supports long-running goals through one persistent
instance and temporary activations. Preserve state, source references, resource
limits, instance isolation, and effect receipts.

Keep the core independent of any particular goal or domain. Use ordinary
nodes, items, intentions, and events before adding a subsystem. The graph is
revisable memory, not an authority over the agent. An activation ends with a
separate state review, but no fixed reasoning sequence or required state edit.

Runtime state belongs under `.concorde2/`. Never commit credentials, instance
data, or private diagnostic traces. Do not migrate an existing instance as an
unrequested side effect of development.

Use the [evaluation guide](docs/evaluation.md) to describe what a check actually
shows. Keep simulated outcomes distinct from external results, and preserve
failures and the conditions under which a result was observed.

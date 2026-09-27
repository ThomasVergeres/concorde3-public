# Design principles

Concorde is built for goals that outlast one model session. It keeps one
persistent identity while each activation handles a limited amount of work.
Memory, attention, and effects carry the work forward. The model can revise a
plan when new evidence arrives.

## Broad and focused attention

The broad-versus-focused idea was inspired by *The Master and His Emissary*.
It is a design metaphor, not a claim about brains or a pair of agents. Broad
attention looks at the wider situation: purpose, commitments, evidence, and
what a plan may be missing. Focused attention works on a specific matter.
Either can change the wider understanding.

The runtime does not assign these roles to node types or model personas. Any
activation can inspect and revise the whole instance. It does reserve an
opportunity to reconsider the wider situation, and each successful work turn
ends with a separate chance to update memory and attention.

## Memory should stay usable

Concorde stores short, connected records instead of carrying a full
conversation into every activation. It shows a small relevant view and lets
the agent follow links, search, or page through older records. A stored belief
can change without erasing its source or earlier version. The graph helps the
agent find evidence; it does not decide what is true.

## Outcomes should change plans

Work, a tool call, and a useful result are different things. Concorde records
effects and observed outcomes so later activations can check what happened.
Waiting, changing direction, or stopping can be sensible choices. The runtime
provides the means to make them; the quality of the choice depends on the
model, its context, and the evidence available.

The [runtime design](design.md) describes the mechanisms. The
[data-engine mapping](data-engine.md) describes how experiments collect and
review their results.

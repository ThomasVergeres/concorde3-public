# Evaluation

The repository has several ways to examine Concorde:

- Go tests check state changes, attention limits, tool behavior, restarts, and effect handling without calling a model.
- `evals/` runs selected tasks with a model in a controlled setting. Each run records its inputs, limits, actions, and results.
- `worlds/` runs longer simulations with other actors and records what happened after Concorde acted.

The optional discrepancy campaign captures running instances at set times. It collects more detail around failures, samples ordinary runs, and groups cases for review. See [the data-engine map](data-engine.md).

For any comparison, choose the task and success criteria before running it. Keep failed and incomplete runs. Record the Concorde version, model, settings, and observation period alongside each result. A simulation result describes that simulation; it does not stand in for use in another environment.

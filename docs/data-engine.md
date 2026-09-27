# Data-engine pieces in Concorde

| Step | What Concorde does |
| --- | --- |
| Observe | Concorde records what an agent did, what it was shown, and what happened next. Simulated worlds record how other actors respond. |
| Collect | During an experiment, a separate monitor saves agent state, events, tool calls, and world results at set times. It saves more detail for failures and samples ordinary runs too. |
| Review | A case list groups repeated signals. A separate reviewer checks the source material and looks for possible agent, tool, or world problems. |
| Test a change | The experiment runs the old and changed versions against the same task, a healthy case, and a new case held back from development. |
| Keep the results | The project records what worked, what failed, and which conclusions the results support. |

# Evaluations

`evals` runs bounded Concorde trials in isolated containers. Cases can supply
history, new observations, controls, and receiving checks. The runner records
the image, fixture, inputs, context, state, traces, and outcomes for each trial.
See [evaluation](../docs/evaluation.md) for how to interpret the results.

## Run a case

The lab needs Linux, Docker, Go, Python 3.10+, a qualified Concorde image,
subscription auth, and permission for the independent stop timer. Build the
image and fixture from the same source revision. Keep auth and output outside
Git.

```bash
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t concorde3:trial .
go build -o bin/lab-fixture ./evals/fixture
python3 -m evals.lab run --output /absolute/new/trial \
  --auth /absolute/auth.json --fixture bin/lab-fixture \
  --image concorde3:trial --models luna6-max \
  --cases AR08 --variants challenge,control \
  --entry episode --starts 1 --draws 1 --wall 1200 --deadline 900
```

`--cases` is required. `--variants`, `--worlds`, `--draws`, and `--models`
select the comparison. A candidate image, fixture, kit, or declared config
change can be supplied with the corresponding `--candidate-*` option. Keep
baseline and candidate inputs matched except for the change being studied.
Use fresh output paths and keep every trial result.

The run stores observations separately from grades. Regrade without replacing
the original record:

```bash
python3 -m evals.audit /absolute/new/trial
```

Trials consume real model calls. The shared admission file defaults to
`~/.local/share/concorde/behavioral-lab/admissions.jsonl`; set
`CONCORDE_LAB_LEDGER` or `--ledger` when several runners must share another
path. The runner also applies per-instance limits and a host cutoff. Its
container and network isolation protect other trials only when the host and
mounts are configured as intended.

Grades distinguish exposure, action, and observed outcome. A passed mechanics
check does not establish that the agent made a good decision. Small simulated
panels do not establish performance in an open environment. Inspect the recorded
trace and receiving result before drawing a behavioral conclusion.

# Simulated worlds

`worlds` runs finite scenarios around Concorde instances. A world controls
counterpart actions, messages, resources, receiving workflows, and a cutoff.
Concorde still owns its goal, state, attention, and tools. The world records
what happened; its observer cannot act for a subject.

Built-in packs cover research, coordination, a small market, and individual
consumers. Market and consumer behavior are scenario fixtures, not a required
purpose for Concorde. The framework uses Python's standard library and SQLite.
Live runs also need Docker, a qualified Concorde image, an authorized Codex
subscription login, and a host able to install the cutoff timer with
`sudo -n systemd-run`.

## Start a world

Run these commands from the repository root. Use a new absolute directory
outside Git for each world. `qualify` checks world mechanics without a model;
`live-qualify` makes subscription calls.

```bash
python3 -m worlds.cli qualify /absolute/new/qualification
python3 -m worlds.cli init /absolute/new/world --pack research --seed 11 --hours 2
python3 -m worlds.cli run /absolute/new/world --image concorde3:trial
python3 -m worlds.cli observe /absolute/new/world
python3 -m worlds.cli freeze /absolute/new/world
```

`run` blocks until cutoff or interruption and freezes the world during cleanup.
Use `CONCORDE_SUBSCRIPTION_AUTH_FILE=/absolute/auth.json` if the login is not at
`$CODEX_HOME/auth.json` or `~/.codex/auth.json`. The auth file is mounted
read-only and must stay outside the repository. Do not start a second controller
for an active world. `attach` is an explicit recovery operation after checking
the stopped controller and any calls in flight. A frozen world cannot resume.

Worlds keep their SQLite state, action receipts, observations, source and image
details, and private model-call records under the chosen root. Keep that root
private. Counterpart calls have per-world and shared limits; reaching a limit
may delay a reply. Treat a delayed reply as missing exposure, not rejection.

## Extend the framework

Trusted operator code can register scenario packs and receiving adapters through
[the scenario module](scenarios.py). The bundled CLI loads built-in packs;
custom packs need a small launcher that registers them before opening a world.
Do not run participant-supplied adapters in the trusted world process.

The [evaluation guide](../docs/evaluation.md) explains how to read simulated
results alongside the runtime's own records.

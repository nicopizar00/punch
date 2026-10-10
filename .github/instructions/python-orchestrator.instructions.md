---
applyTo: "src/punch/**,bin/punch"
description: Behavior rules for the Python orchestrator (bin/punch and src/punch).
---
# Python Orchestrator — Path Instructions

Scope: `bin/punch` and all under `src/punch/`.

## Rules

- **Pinned runtime.** Docker, Python 3.10+, and the pinned
  `requirements.txt` are host prerequisites. Punch orchestration is Python
  plus PyYAML; all other orchestration logic remains standard-library based.
  Installation is an explicit setup step, never an action of `punch run`.
- **Single responsibility — orchestration.** Orchestrator own control
  flow: arg parse, workflow launch, logs, dataset harvesting and preflight,
  exit-code propagation, and evidence write. `src/punch/execution.py` owns
  launch, logs, confirmation, and data harvesting. It does not own Docker
  semantics, k6 thresholds,
  or report HTML — those belong to compose, k6 scripts,
  `src/tests/support/`.
- **Stream subprocess output separately, no buffer.** Every `Popen` reads
  `stdout` and `stderr` line by line and forwards each to its matching terminal
  stream. Only stdout may carry `[DATA <dataset>]` records; stderr is never harvested as
  CSV. Native command output reaches the terminal live and the run log.
- **Exit codes propagate.** CLI exit code = child process exit
  code (or first non-zero in sequence). Never swallow non-zero.
- **Evidence artifact mandatory.** Every `run` writes
  `reports/state/punch-run.json` with tests, per-workflow `exitCode`,
  `passed`, `failure`, and `datasets`, plus overall
  `exitCode`, `passed`, `startedAt`, and `durationSeconds`. Write even on
  failure.
- **Artifact paths explicit.** Use `pathlib.Path`, anchor every path
  to `REPO_ROOT`. No relative `./reports/`; no `os.getcwd()` surprise.
- **Docker Compose invocations consistent.** One selected workflow equals one
  explicit `docker compose ... run --rm <service>` invocation (never
  `docker run`); use the workflow's declared working directory and compose
  file. `punch run` does not build images.
- **Bash thin-wrapper principle.** `bin/punch` exec Python module —
  no logic, no path massage, no env default. Logic in
  `bin/` → move to `src/punch/`.
- **Data prompts only.** Interactive prompting is allowed only for the
  per-dataset produce opt-in (menu) and the consumed-data delete prompt;
  non-interactive runs opt in with `--produce` and never delete. Bundled
  workflows declare no data. No other TTY assumption or terminal-colour gating.
- **No new subcommands without a plan.** Adding a command changes the
  public CLI.

## Tests

`python3 -m punch --help` and dry `doctor` = smoke tests. Heavier
tests deferred till CLI grow beyond small command set.

## Streaming subprocess pattern

The canonical execution implementation lives in `src/punch/execution.py`.
It uses line-buffered text streams and independent stdout/stderr readers. The
only output parsing is the workflow-declared `[DATA <dataset>]` stdout
contract: tagged records for opted-in datasets are validated against the
declared columns and collected in order; console summaries never control
execution. Each dataset is written to a temporary sibling and atomically
published only after a zero exit code and at least one valid record. Sequential execution is
the contract; parallel runs require a Plan.

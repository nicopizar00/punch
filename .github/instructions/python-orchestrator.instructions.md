---
applyTo: "src/punch/**,bin/punch"
description: Behavior rules for the Python orchestrator (bin/punch and src/punch).
---
# Python Orchestrator — Path Instructions

Scope: `bin/punch` and `src/punch/`. Module map:
[reference architecture §1](../../docs/architecture/reference-architecture-and-implementation-guide.md#1-solution).

## Rules

- **Pinned runtime.** Python 3.10+ with the pinned `requirements.txt`
  (PyYAML for loading; simple-term-menu and rich for the menu). Everything
  else is standard library. Installation is a setup step, never part of
  `punch run`.
- **Orchestration only.** The engine owns argument parsing, loading,
  planning, launch, logs, dataset preflight and harvesting, exit codes, and
  evidence. Docker semantics belong to Compose, thresholds to k6 scripts,
  report HTML to `src/tests/support/`.
- **Pure planners.** `data_plan.py` and `sizing.py` never import presentation;
  `menu.py` and `__main__.py` are the adapters.
- **One compose run per Punch job.** `docker compose -f <file> run --rm
  <service>` from the job's working directory; never `docker run`, never
  a build. Sequential only; parallel runs need a plan.
- **Stream, don't buffer.** Read stdout and stderr line by line, forward each
  to its terminal stream, and write both to `reports/logs/k6-<job>.log`.
  Only stdout `[DATA <dataset>]` lines for opted-in datasets are data; they
  are column-checked into a temp sibling and published atomically after a zero
  exit with ≥ 1 row.
- **Exit codes propagate.** CLI exit = the child's code (first non-zero in a
  sequence). Never swallow one.
- **Evidence always.** Every `run` writes `reports/state/punch-run.json`, on
  failure too. Schema: [validation.md](../../docs/jobs/validation.md).
- **Explicit paths.** `pathlib.Path` anchored to `REPO_ROOT` or the job's
  working directory; no `os.getcwd()`.
- **Bash stays thin.** `bin/punch` execs `python3 -m punch`; no logic.
- **Prompts are scoped.** Interactive input only on a TTY: the menu, the
  data-source and producer pickers, the menu's Docker-run and dataset-write
  confirmations, and the consumed-data delete prompt. `--no-input` and
  non-TTY runs never block.
- **No new subcommand without a plan.** It changes the public CLI.

## Tests

`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'` — one
`tests/test_<module>.py` per engine module, plus CLI, CI-contract, and
job-coverage tests. CI runs `ruff check src tests` and `ruff format --check src tests`
(ruff 0.7.4; `ruff.toml`: default rules plus import sorting, line length 100)
before the suite.

# k6-ts-docker

A didactic performance testing playground for k6 written in TypeScript, packaged in Docker, and executed via GitHub Actions.

## Goal

Demonstrate a maintainable, end-to-end performance testing pipeline — from a multi-service reference application through to GitHub Actions artifact transfer — that is easy to read, extend, and adopt.

## Architecture

Start with the
[Reference Architecture and Implementation Guide](docs/architecture/reference-architecture-and-implementation-guide.md)
for the solution model, option-versus-target selection semantics, design
trade-offs, and a clean-room reproduction path for restricted enterprise
environments. The shorter
[Architectural Boundaries](docs/architecture/punch-boundaries.md) document remains
the ownership map for contributors.

## Quick start

Requires Docker, Python 3.10+, and the pinned Python requirements. No Node or
k6 is required on the host. Install the declared runtime before running Punch;
`punch run` never installs dependencies or builds images.

```bash
python3 -m pip install -r requirements.txt
docker compose build
./bin/punch run smoke
./bin/punch run path/to/workflow.yaml
./bin/punch run path/to/producer.yaml --produce orders
./bin/punch run path/to/consumer.yaml --data orders=data/batch-2.csv
./bin/punch run path/to/workflow.yaml --config options/5-vu-5m.json   # k6 run --config
./bin/punch run path/to/producer.yaml --size-for consumer   # size it for consumer's k6 config
./bin/punch menu path/to/workflows-dir   # interactively pick + run a workflow
```

Each YAML definition in `workflows/k6/*.yaml` produces one explicit Compose
run.

The load shape is a native k6 options JSON passed with `k6 run --config`:

```yaml
spec:
  k6:
    script: /scripts/orders.js
    config: options/5-iterations.json   # optional default, beneath workingDirectory
```

Punch bind-mounts the selected file read-only at `/punch/k6-config.json` and
appends `--config /punch/k6-config.json` to the k6 command. `--config <path>`
(or a menu preset from the `options/` directory beside the workflows
directory) replaces `spec.k6.config` for one run; with neither, no config is
passed. Script `options` take precedence over a config file in k6, so a
script that should take its shape from the config must not export
`scenarios`, `vus`, `iterations`, `duration`, or `stages`. The file must be a
JSON object; Punch checks that before Docker.

Workflows exchange data through named datasets declared in `spec.data`:

```yaml
spec:
  sizing:                    # optional; see "Sizing for a target"
    iterationSeconds: 1.0    # one iteration on one VU
    maxSeconds: 270          # budget when sized as a producer
    margin: 0.15             # extra rows when sized for as a target
  data:
    directory: data            # host dir, beneath workingDirectory
    mountedAt: /scripts/data   # same dir inside the container
    produces:
      - dataset: orders
        columns: [orderId]
        targets: [order-status]
        recommended: true   # optional
    requires: [carts]
    optional: [extras]       # used when present, skipped otherwise
```

- A producer prints `[DATA <dataset>] <csv payload>` on stdout. Rows are
  written to `<directory>/<dataset>.csv` (with a header) only when the run opts
  in with `--produce <dataset>` (or `--produce all`), each row has the declared
  column count, and the run succeeds; the file is published atomically, so a
  failed run keeps the previous file. Stderr is never harvested.
- A consumer is preflighted before Docker: each required dataset file must
  have at least one row, or the run fails naming its producers. Punch injects
  `DATA_<DATASET>_CSV=<container path>`; `--data <dataset>=<path>` reads an
  alternate file beneath `directory`. Interactive runs are offered a delete
  prompt for consumed data; non-interactive runs keep it.
- In a terminal, `punch run <workflow>` and the `punch` menu settle data
  before Docker (`punch.data_plan`). Each optional dataset with more than one
  available source offers `default (built-in)` or its data file (cursor on
  default). Then the normal preflight runs; when a dataset the run reads has
  no rows, an arrow-key list of every producer opens — the one whose product
  sets `recommended: true` labeled and preselected (first by name if
  several), producers missing required environment tagged `needs <VAR>`.
  `punch run` runs the picked one with `--produce <dataset>`; the menu
  continues exactly as if it had been picked directly (it asks before
  writing) — still one Compose run — and walks further when it is missing
  data too; the hint names the workflow
  to re-run and what it still misses. Esc cancels. Non-interactive
  equivalents: `--no-input`, `--data <dataset>=default`,
  `--data <dataset>=<path>`, or run the producer with `--produce`.
- **Sizing for a target.** A producer that declares `spec.sizing`
  `iterationSeconds` + `maxSeconds` can be sized for any
  `produces[].targets` workflow that declares `spec.sizing`. Punch reads the
  target's load shape from a k6 config — one scenario (`shared-iterations`:
  `iterations`; `per-vu-iterations`: `vus × iterations`; `constant-vus`:
  `vus × duration` over the target's `iterationSeconds`) or the equivalent
  top-level `vus`/`iterations`/`duration` shortcuts — adds the target's
  `margin`, and runs the producer with a copy of its own `spec.k6.config`
  whose execution becomes `shared-iterations` with
  `iterations=⌈rows × (1 + margin)⌉` and just enough `vus` to finish inside
  `maxSeconds` (the scenario's name, `options`, `tags`, `env`, `exec`, and
  `maxDuration` are kept). The generated file is written to
  `reports/state/k6-config-<producer>.json` and passed as `--config`. The
  menu offers `Options as usual` / `Size for a target workflow` (when
  `options/` has presets) and asks for the target's preset;
  `--size-for <target>` reads the target's shape from `--config`, else the
  target's `spec.k6.config`. Fewer produced rows than the target needs
  prints a warning; the exit code is unchanged.
- An optional dataset (`optional: [...]`) is used when its file has at least
  one row: Punch injects `DATA_<DATASET>_CSV` as for a required one. When the
  file is missing or header-only, the variable is left unset, the run
  continues, and Punch prints
  `[punch] optional dataset "<name>" not used — scenario uses its default`.
  The automatic rule applies unless a source is picked interactively or
  `--data <dataset>=default` / `--data <dataset>=<path>` is given.
  `--data` and the delete prompt work the same; a producer's `targets` may
  name an optional consumer, and an optional dataset needs no producer.
- Every workflow YAML in one directory forms a catalog. Punch checks that each
  target exists and requires or optionally consumes the dataset, that every
  required dataset has a producer, and that producers of one dataset agree on
  columns.

A prior data file is not evidence that the current run succeeded; inspect the
current run evidence instead. Bundled workflows declare no data.

The legacy bash scripts (`./bin/test-smoke`, `./bin/test-gate`,
`./bin/test-journey`, `./bin/test-suite`, `./bin/build`, `./bin/clean`)
still work and remain supported until the Python CLI reaches full parity.

## Reference application

The suite runs against a small four-service reference app:

| Service | Port | Role |
|---|---|---|
| `gateway-api` | 3000 | BFF / API gateway — proxies catalog and order requests |
| `catalog-api` | 3001 | Read-only product catalog — demonstrates GET gates |
| `orders-api` | 3002 | Create/read orders backed by Postgres |
| `postgres` | 5432 | Relational persistence with healthcheck and seed schema |

## k6 test suite

| Test | What it demonstrates |
|---|---|
| `smoke` | All services are reachable and healthy |
| `catalog-gate` | p95 latency and error-rate thresholds on catalog reads |
| `order-journey` | Create an order, read it back, validate consistency; writes a state file |
| `bff-checkout-journey` | End-to-end BFF checkout journey against an external target; writes a state file |

## Execution chain

```
TypeScript source  →  esbuild (inside Docker)  →  k6 image  →  run  →  reports
```

Every change preserves this linear pipeline.

## Reports and artifacts

After a test run, `reports/` contains:

```
reports/
  smoke-report.html
  smoke.json
  catalog-gate-report.html
  catalog-gate.json
  order-journey-report.html
  order-journey.json
  state/
    test-context.json        # serialized journey metadata
  logs/
    gateway-api.log
    catalog-api.log
    orders-api.log
    postgres.log
```

GitHub Actions uploads all of these as the `performance-suite-reports` artifact. A second CI job downloads the artifact and validates that every expected file is present — demonstrating serialized state transfer between jobs without live containers.

`reports/state/punch-run.json` records per-workflow `exitCode`, `passed`,
`failure` and `datasets`, plus the overall run outcome and
timing. This is the evidence for the current run.

## AI-assisted operating model

This repo uses a linear, risk-scaled lifecycle for AI-assisted changes —
**Spec → Plan → Build → Test → Review → Ship**. It is a process: Punch ships
always-on and path-scoped Copilot instructions, no prompts, agents, or skills.

- Operating model: [`docs/ai/operating-model.md`](docs/ai/operating-model.md)
- Model selection: [`docs/ai/model-selection.md`](docs/ai/model-selection.md)
- Change cascade: [`docs/ai/maintenance-matrix.md`](docs/ai/maintenance-matrix.md)
- Layered architecture: [`docs/architecture/punch-boundaries.md`](docs/architecture/punch-boundaries.md)
- Validation contract: [`docs/workflows/validation.md`](docs/workflows/validation.md)
- Contribution rules: [`CLAUDE.md`](CLAUDE.md)

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines, local commands, and branch/PR conventions. Small, focused PRs are preferred; run `./bin/punch run smoke` to validate basic health before opening a PR.

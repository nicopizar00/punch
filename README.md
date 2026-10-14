# k6-ts-docker

Punch (`bin/punch`, `src/punch/`) turns one declarative workflow YAML into one
Docker Compose run of k6. This repository also carries TypeScript k6 tests and
a small reference app so the whole pipeline runs end to end, locally and in
GitHub Actions.

```
TypeScript source  →  esbuild (inside Docker)  →  k6 image  →  run  →  reports
```

## Quick start

Host needs Docker, Python 3.10+, and the pinned `requirements.txt`. No Node or
k6. `punch run` never installs dependencies or builds images.

```bash
python3 -m pip install -r requirements.txt
docker compose build
./bin/punch doctor
./bin/punch run smoke
```

## Commands

| Command | Does |
|---|---|
| `punch doctor` | Check Docker, Compose, Python, and the bundled workflows |
| `punch run <name\|path.yaml>` | Run one bundled workflow or any workflow YAML |
| `punch run all` | Run every bundled workflow in order; skips one whose required environment is unset |
| `punch menu [workflows-dir]` | Pick and run a workflow interactively (default: `workflows/k6/`) |
| `punch clean` | `docker compose down --volumes --remove-orphans` |

`punch run` flags:

| Flag | Effect |
|---|---|
| `--config <path>` | k6 options JSON for this run (replaces `spec.k6.config`) |
| `--produce <dataset\|all>` | Write a declared dataset; repeatable. Without it nothing is written |
| `--data <dataset>=<path\|default>` | Read a dataset from another file beneath `spec.data.directory`, or use an optional dataset's built-in default |
| `--size-for <target>` | Size this producer run for `<target>`; implies `--produce` for the datasets it feeds |
| `--no-input` | Never open the data-source or producer pickers |
| `--collect-logs` | After the run, write service logs to `reports/logs/` |
| `--keep-going` | With `all`, continue after a failure |

`--produce`, `--data`, and `--size-for` apply to one workflow, not `all`.
`./bin/test-smoke`, `test-gate`, `test-journey`, `test-suite`, `build`, and
`clean` are thin wrappers over `punch run` and `docker compose`.

## Workflow YAML

```yaml
apiVersion: punch/v1
kind: K6Workflow
metadata:
  name: orders
  description: Create orders.
spec:
  workingDirectory: ../..                       # every path below resolves beneath it
  compose: { file: docker-compose.yml, service: k6 }
  k6:
    script: /scripts/orders.js
    config: options/5-iterations.json           # optional default load shape
  environment: { forward: [BASE_URL], required: [BASE_URL] }
  sizing:                                       # optional
    iterationSeconds: 1.0                       # one iteration on one VU
    maxSeconds: 270                             # time budget when sized as a producer
    margin: 0.15                                # extra rows when sized for as a target
  data:                                         # optional
    directory: data                             # host dir
    mountedAt: /scripts/data                    # same dir inside the container
    produces:
      - { dataset: orders, columns: [orderId], targets: [order-status], recommended: true }
    requires: [carts]                           # must have rows before Docker starts
    optional: [extras]                          # used when present, skipped otherwise
```

Loading is strict: unknown or duplicate keys, bad names, missing files, and
paths escaping `workingDirectory` fail before any run. Every YAML in one
directory forms a catalog; Punch checks that targets exist and consume the
dataset, every required dataset has a producer, and producers of a dataset
agree on columns. Bundled workflows declare no data.

### Load shape

The load shape is a native k6 options JSON. Punch bind-mounts it read-only at
`/punch/k6-config.json` and appends `--config /punch/k6-config.json`.
`--config` (or a menu preset from `options/` beside the workflows directory)
replaces `spec.k6.config` for one run; with neither, no config is passed. k6
lets script options override a config file, so a script that takes its shape from the config must not export
`scenarios`, `vus`, `iterations`, `duration`, or `stages`.

### Datasets

- **Produce.** A producer prints `[DATA <dataset>] <csv row>` on stdout. Rows
  are written to `<directory>/<dataset>.csv` (header from `columns`) only with
  `--produce`, only after a successful run with ≥ 1 valid row, and atomically,
  so a failed run keeps the previous file. Stderr is never harvested.
- **Consume.** Each required dataset needs ≥ 1 row before Docker starts, or
  the run fails naming its producers. Punch injects
  `DATA_<DATASET>_CSV=<container path>`. An optional dataset with no rows is
  left unset and Punch prints
  `[punch] optional dataset "<name>" not used — scenario uses its default`.
  Interactive runs offer to delete consumed data; non-interactive runs keep it.
- **Pickers (terminal only).** Before Docker, each optional dataset with more
  than one source offers `default (built-in)` or its file. When a dataset the
  run reads has no rows, a list of its producers opens (the `recommended` one
  preselected, ones missing required environment tagged `needs <VAR>`);
  picking one runs that producer instead (`punch run` writes its dataset; the
  menu asks first), still one Compose run, and Punch names the workflow to run
  next. Esc cancels; `--no-input` skips pickers.
- **Sizing for a target.** A producer with `spec.sizing` can be sized for any
  `targets` workflow that also declares it. Punch reads the target's shape
  from a k6 config (`shared-iterations`, `per-vu-iterations`, `constant-vus`,
  or top-level `vus`/`iterations`/`duration`), adds its `margin`, and runs the
  producer with a generated `shared-iterations` config
  (`reports/state/k6-config-<producer>.json`) with enough VUs to finish inside
  `maxSeconds`. Menu: `Size for a target workflow`; CLI: `--size-for <target>`
  (shape from `--config`, else the target's `spec.k6.config`). Fewer rows than
  needed prints a warning; the exit code is unchanged.

A data file left from an earlier run is not evidence; read
`reports/state/punch-run.json`.

## Reference app and bundled workflows

| Service | Port | Role |
|---|---|---|
| `gateway-api` | 3000 | BFF: proxies catalog and orders |
| `catalog-api` | 3001 | Read-only in-memory catalog |
| `orders-api` | 3002 | Create/read orders in Postgres |
| `postgres` | 5432 | Persistence, seeded by `docker/postgres/init.sql` |

| Workflow | Script | Demonstrates |
|---|---|---|
| `smoke` | `smoke.ts` | Every service is reachable and healthy |
| `gate` | `catalog-gate.ts` | Latency, error-rate, and check thresholds on catalog reads |
| `journey` | `order-journey.ts` | Create an order, read it back; writes `state/test-context.json` |
| `bff-checkout-journey` | `bff-checkout-journey.ts` | Checkout through an external BFF; requires `TARGET_BASE_URL` |

## Artifacts

Each run writes `reports/state/punch-run.json` (the run evidence),
`reports/logs/k6-<workflow>.log`, and each script's `<script>-report.html`
and `<script>.json`. CI uploads `reports/` as `performance-suite-reports`. Schema and
evidence rules: [validation.md](docs/workflows/validation.md).

## Docs

| Doc | For |
|---|---|
| [Reference architecture](docs/architecture/reference-architecture-and-implementation-guide.md) | Components, contracts, design decisions, reproducing the core |
| [Boundaries](docs/architecture/punch-boundaries.md) | Layer ownership and anti-patterns |
| [Validation](docs/workflows/validation.md) | Evidence contract |
| [Decisions](docs/decisions/) | ADRs |
| [Specs](docs/specs/) | Feature specs: data pickers, target sizing, native k6 config |
| [CLAUDE.md](CLAUDE.md) | Project rules |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branches, PRs |

## Kubernetes runtime

`k8s/` runs the same workflows natively on Argo Workflows — no Python in the
cluster, datasets and evidence in S3. The contract, the consumer image and
cluster prerequisites, the mapping from a workflow YAML to a thin template,
running and chaining workflows, Argo CD, AWS, the CI design, and
troubleshooting: [`k8s/README.md`](k8s/README.md). Step mapping from the
local orchestrator, GitHub Actions parity, dataset lifetime, and other
clouds:
[`docs/architecture/orchestrator-to-kubernetes.md`](docs/architecture/orchestrator-to-kubernetes.md).

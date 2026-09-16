# k6-ts-docker

A didactic performance testing playground for k6 written in TypeScript, packaged in Docker, and executed via GitHub Actions.

## Goal

Demonstrate a maintainable, end-to-end performance testing pipeline — from a multi-service reference application through to GitHub Actions artifact transfer — that is easy to read, extend, and adopt.

## Quick start

Requires Docker, Python 3.10+, and the pinned Python requirements. No Node or
k6 is required on the host. Install the declared runtime before running Punch;
`punch run` never installs dependencies or builds images.

```bash
python3 -m pip install -r requirements.txt
docker compose build
./bin/punch run smoke
./bin/punch run path/to/workflow.yaml
./bin/punch run path/to/csv-workflow.yaml --confirm-output-data
./bin/punch menu path/to/workflows-dir   # interactively pick + run a workflow
```

Each YAML definition in `workflows/k6/*.yaml` produces one explicit Compose
run. CSV output is optional, workflow-declared, and path-configured. When
declared, `src/punch/execution.py` collects the ordered, tag-stripped `[CSV]`
stdout records and publishes the CSV only after a successful run. A prior CSV
file is not evidence that the current run succeeded; inspect the current run
evidence instead. Bundled workflows do not declare CSV output.

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
`failure`, `csvPath`, and `csvRecordCount`, plus the overall run outcome and
timing. This is the evidence for the current run.

## AI-assisted operating model

This repo uses a linear lifecycle for AI-assisted changes — **Spec →
Plan → Build → Test → Review → Ship** — plus one orthogonal maintenance
prompt, **punch-document**, for recurring documentation reconciliation. Each lifecycle
phase has one prompt; Build is a single `punch-build` prompt whose
`punch-builder` dispatcher routes to one of two domain engineers. Domain +
lifecycle skills and the agent personas support the lifecycle — see the
registries below for the live inventory.

- Operating model: [`docs/ai/operating-model.md`](docs/ai/operating-model.md)
- Scoped-build policy: [`docs/ai/scoped-build-policy.md`](docs/ai/scoped-build-policy.md)
- Model selection: [`docs/ai/model-selection.md`](docs/ai/model-selection.md)
- Mode mapping: [`docs/ai/copilot-mode-mapping.md`](docs/ai/copilot-mode-mapping.md)
- Skill registry: [`docs/ai/skill-registry.md`](docs/ai/skill-registry.md)
- Prompt registry: [`docs/ai/prompt-registry.md`](docs/ai/prompt-registry.md)
- Layered architecture: [`docs/architecture/punch-boundaries.md`](docs/architecture/punch-boundaries.md)
- Validation contract: [`docs/workflows/validation.md`](docs/workflows/validation.md)
- Contribution rules: [`CLAUDE.md`](CLAUDE.md)

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines, local commands, and branch/PR conventions. Small, focused PRs are preferred; run `./bin/punch run smoke` to validate basic health before opening a PR.

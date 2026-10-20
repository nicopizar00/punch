# Validation

A change is done when it has produced the evidence its class requires.

## Evidence matrix

| Change class | Touches | Required evidence |
|---|---|---|
| **Runtime-affecting** | `src/**`, `docker/**`, `docker-compose.yml`, `bin/**`, `jobs/**`, `.github/workflows/**`, or any runtime/artifact contract | `./bin/punch run <job>` → `reports/state/punch-run.json` with `"passed": true` |
| **Documentation-only** | `*.md`, `docs/**`, `.github/instructions/**` with no contract impact | Diff review and a link check; no run expected |

A change that mixes both is runtime-affecting.

## Run evidence: `reports/state/punch-run.json`

Written by `src/punch/__main__.py` on every `punch run`, pass or fail. The
menu's `Run job` prints results to the terminal and writes no evidence; its
`Run workflow` writes
[`punch-workflow.json`](#workflow-evidence-reportsstatepunch-workflowjson).

```json
{
  "command": "run",
  "tests": ["smoke", "gate", "journey", "bff-checkout-journey"],
  "results": [
    {"test": "smoke", "job": "jobs/k6/smoke.yaml", "exitCode": 0,
     "childExitCode": 0, "passed": true, "failure": null, "datasets": []}
  ],
  "exitCode": 0,
  "passed": true,
  "startedAt": "<ISO-8601 UTC>",
  "durationSeconds": 78.3
}
```

Per result:

| Key | Meaning |
|---|---|
| `exitCode` | Effective code: the child's, or `1` for a failure before Docker |
| `childExitCode` | The k6 container's code; `null` when no container started |
| `failure` | Why it failed (`null` on success) |
| `datasets` | `dataset`, `path`, `recordCount`, `published` per produced dataset |
| `skipped` | `true` when `all` skipped the job for missing required environment |
| `switchedFrom` | Jobs a producer pick replaced, in order |
| `dataSources` | `{"<dataset>": "default" \| "<path>"}` for each dataset read |
| `config` | k6 options JSON passed as `--config`; absent when none |
| `sizing` | With `--size-for`: `target`, `datasets`, `shape`, `preset`, `rowsNeeded`, `margin`, `producerIterations`, `producerVus`, `producedRows`, `short` |

The last five appear only when they apply. A data file left by an earlier run
is not evidence for this one.

## Workflow evidence: `reports/state/punch-workflow.json`

Written by `punch.workflow.run_workflow` for the menu's `Run workflow`: before
step 1, after every step, and on interruption. Paths are relative to the
current directory when beneath it, absolute otherwise.

```json
{
  "version": 1,
  "outcome": "passed",
  "passed": true,
  "jobs": ["http-cart", "http-orders", "http-orders-status"],
  "steps": [
    {"step": 1, "job": "http-cart", "load": "auto",
     "label": "auto: sized for step 2 (7 iterations, 1 VU)",
     "config": "reports/state/k6-config-http-cart.json", "sizing": {"...": "..."},
     "produce": ["carts"], "reads": {}, "builtIn": [],
     "status": "passed", "exitCode": 0, "failure": null, "rows": {"carts": 7}}
  ],
  "warnings": []
}
```

| Key | Meaning |
|---|---|
| `outcome` | `running`, `passed`, `failed`, or `interrupted` |
| `passed` | `true` only when `outcome` is `passed` |
| `steps[].load` | `auto`, `job default`, or the options file |
| `steps[].label` | The plan's load text for the step |
| `steps[].config` | The file passed as `--config`; `null` for script options |
| `steps[].sizing` | For a sized step: the `sizing` keys `punch-run.json` records; `producedRows` and `short` are `null` until the step runs |
| `steps[].status` | `pending`, `passed`, `failed`, `skipped`, or `interrupted` |
| `steps[].exitCode` | The k6 container's code; `null` when no container started |
| `steps[].rows` | Rows published per dataset; a failed step publishes none |
| `warnings` | Plan warnings, then `rows repeat` warnings |

A menu workflow change is verified when this file shows `"passed": true`.

## Other artifacts

| Path | Written by |
|---|---|
| `reports/logs/k6-<job>.log` | Punch: the k6 container's stdout and stderr |
| `reports/<script>-report.html`, `reports/<script>.json` | Each k6 script's `handleSummary` (`smoke`, `catalog-gate`, `order-journey`, `bff-checkout-journey`) |
| `reports/state/test-context.json` | `order-journey`, `bff-checkout-journey` |
| `reports/state/k6-config-<producer>.json` | Punch, for a sized producer run or a sized workflow step |
| `reports/logs/{gateway-api,catalog-api,orders-api,postgres}.log` | Punch, with `--collect-logs` |

## Validate locally

```bash
./bin/punch doctor
./bin/punch run smoke
./bin/punch run all --collect-logs
cat reports/state/punch-run.json        # "passed": true
```

`all` skips `bff-checkout-journey` unless `TARGET_BASE_URL` is set.

Failure path:

```bash
TARGET_BASE_URL=http://does-not-exist.invalid ./bin/punch run smoke || echo "failed as expected"
cat reports/state/punch-run.json        # "passed": false, non-zero exitCode
```

If `punch-run.json` is missing, the change has not been verified.

## CI

`.github/workflows/k6.yml` installs `requirements.txt`, lints `src` and `tests`
with ruff 0.7.4 (Ruff's default rules plus import sorting,
[`ruff.toml`](../../ruff.toml)), checks their formatting (`ruff format --check`,
line length 100), runs the Python unit suite, builds images, runs `./bin/punch run all`, collects service logs, and
uploads `reports/` as `performance-suite-reports`. A second job,
`validate-artifacts`, downloads that artifact without live containers and
fails if an expected file is missing. `tests/test_ci_contract.py` pins the
first job's step order.

## Not evidence

- A green terminal alone.
- A passing unit test alone (it does not exercise the stack).
- A claim in a PR description.

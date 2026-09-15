# Validation Workflow

A change is not "done" until it has produced **validation evidence**. This
file specifies what evidence looks like and how to produce it.

## Evidence matrix (canonical — link here, don't restate)

Every change falls into exactly one class. This is the single source of
truth Rules 3/13, Review, Ship, the review skill, and the PR template all
point back to instead of each re-asserting "evidence mandatory."

| Change class | Touches | Build? | Required evidence |
|---|---|---|---|
| **Runtime-affecting** | `src/**`, `docker/**`, `docker-compose.yml`, `bin/**`, `.github/workflows/**`, or anything with a runtime/artifact contract | Yes | `./bin/punch run <test>` → `reports/state/punch-run.json` (`passed: true`) |
| **Documentation / Copilot-only** | `README.md`, `docs/**`, `.github/instructions/**`, `.github/prompts/**`, `.github/skills/**`, `.github/agents/**`, `copilot-instructions.md` — no runtime-contract impact | No (straight Plan → PR, per [`documentation.instructions.md`](../../.github/instructions/documentation.instructions.md#build-prompt)) | Diff review + frontmatter/registry parity (`punch-ai-governance` clean) + local link check. No `reports/state/punch-run.json` — none was produced, and none is expected. |

A change that mixes both classes (e.g. a doc update that also changes a
runtime contract) is Runtime-affecting for evidence purposes.

## Evidence contract (Runtime-affecting class)

Every Verify run writes a single canonical artifact:

```
reports/state/punch-run.json
```

Schema (informal — produced by `src/punch/__main__.py`):

```json
{
  "command": "run",
  "tests": ["smoke", "gate", "journey"],
  "results": [
    {"test": "smoke", "workflow": "workflows/k6/smoke.yaml", "exitCode": 0,
     "passed": true, "failure": null, "csvPath": null, "csvRecordCount": 0}
  ],
  "exitCode": 0,
  "passed": true,
  "startedAt": "<ISO-8601 UTC timestamp>",
  "durationSeconds": 78.3
}
```

`failure`, `csvPath`, and `csvRecordCount` are per-workflow evidence fields.
They make missing CSV confirmation, child-process failures, and published CSV
counts auditable without treating a prior data file as proof of this run.

## Host setup and workflow selection

Docker, Python 3.10+, and the pinned requirements are prerequisites. Install
and build explicitly; `punch run` does neither. Execution definitions live in
`workflows/k6/*.yaml`, and one selected workflow produces one explicit Compose
run.

```bash
python3 -m pip install -r requirements.txt
docker compose build
./bin/punch run smoke
./bin/punch run path/to/workflow.yaml
./bin/punch run path/to/csv-workflow.yaml --confirm-output-data
```

CSV is optional, workflow-declared, and path-configured. Its producer is
`src/punch/execution.py`; its schema is the ordered tag-stripped `[CSV]`
stdout records. The file is published atomically only after success. CI must
pass `--confirm-output-data` when it selects a CSV workflow. The bundled
workflows declare no CSV output, so the default CI command remains
`./bin/punch run all`.

In addition, each test writes its own evidence under `reports/`:

| Test | Artifacts |
|---|---|
| smoke   | `smoke-report.html`, `smoke.json` |
| gate    | `catalog-gate-report.html`, `catalog-gate.json` |
| journey | `order-journey-report.html`, `order-journey.json`, `state/test-context.json` |

When `--collect-logs` is set, `reports/logs/{gateway,catalog,orders,postgres}.log`
are also written.

## How to validate the MVP locally

```bash
bin/punch doctor              # Confirm host has docker + python
bin/punch run smoke           # Fastest signal that the stack is healthy
bin/punch run all --collect-logs
cat reports/state/punch-run.json
```

The final `cat` must show `"passed": true`.

## How to validate failure behavior

```bash
TARGET_BASE_URL=http://does-not-exist.invalid bin/punch run smoke || echo "FAILED as expected"
cat reports/state/punch-run.json
```

The artifact must show `"passed": false` and a non-zero exit code. If the
artifact is missing, the orchestrator violated the "evidence first" rule.

## How to validate AI configuration

```bash
ls .github/copilot-instructions.md
ls .github/instructions/*.instructions.md
ls .github/prompts/*.prompt.md
ls .github/skills/*/SKILL.md
```

Then invoke the Review phase (`punch-review` prompt), which activates
the `punch-ai-governance` agent when `.github/` or `docs/ai/` is
touched. Its expected output is a findings list ending with "Governance
is clean".

## How CI re-validates

The GitHub Actions workflow at `.github/workflows/k6.yml` runs the full
suite, collects logs, uploads `reports/` as the `performance-suite-reports`
artifact, then a second job validates the artifact contents are present.

This proves the local validation contract and the CI validation contract
produce the same evidence.

CI first runs `python -m pip install -r requirements.txt` and the direct
Python unit suite, then builds images and invokes `./bin/punch run all`.

## What evidence is NOT

- A green local terminal alone (transient, not auditable).
- A passing unit test in isolation (does not exercise the stack).
- A PR description claim ("I ran it and it worked").

If `reports/state/punch-run.json` does not exist, the change has not been
verified.

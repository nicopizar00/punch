---
applyTo: "src/tests/support/**,src/punch/**,reports/**,docs/workflows/validation.md"
description: Contract for artifacts, logs, summaries, and reports produced by Punch.
---
# Artifacts & Reporting — Path Instructions

Scope: producer side of every artifact Punch emit (`src/tests/support/`,
reporting/state code in `src/punch/`), artifact dir
(`reports/`), validation docs explain it
(`docs/workflows/validation.md`).

## What counts as an artifact

| Artifact | Path | Producer | Reader |
|---|---|---|---|
| Run evidence (machine) | `reports/state/punch-run.json` | `src/punch/__main__.py` | CI validation job, automation |
| Optional CSV data | workflow-declared path | `src/punch/execution.py` | Explicit downstream consumer |
| Journey context (machine) | `reports/state/test-context.json` | `src/tests/order-journey.ts` | Subsequent journey assertions |
| HTML report (per test) | `reports/<test>.html` | `src/tests/support/report.ts` | Humans |
| JSON summary (per test) | `reports/<test>.json` | k6 `handleSummary` | Humans + automation |
| Docker logs | `reports/logs/<service>.log` | `src/punch/` with `--collect-logs` | Humans debugging failures |

## Rules

- **Stable artifact paths.** Five paths above part of public
  contract. Rename or split any = contract change — must
  update `docs/ai/maintenance-matrix.md` and every downstream
  consumer (CI workflow, this file, relevant skill).
- **Low-noise terminal output by default.** Terminal show progress
  and pass/fail, not every line of every container log. Full output
  go to `reports/logs/` when `--collect-logs` set.
- **Full logs to files, always.** Even when terminal quiet, log
  file complete. User debugging failure never need re-run
  to get missing detail.
- **JSON summaries are compact and stable-schema.** No raw k6 per-iteration
  dumps. Summary hold aggregates (counts, thresholds, durations) in
  shape that survive k6 upgrades.
- **HTML reports are self-contained.** Single `.html` file per test;
  no external CSS, no remote fetches, no `file://` assumptions.
- **State files are the canonical run record.** `passed: true|false` in
  `reports/state/punch-run.json` = verification gate; HTML for
  humans, JSON summaries for human inspection or future automation.
- **Naming convention.** `<test>.html`, `<test>.json` under `reports/`;
  `<service>.log` under `reports/logs/`. No date stamps or
  hashes in filenames — break CI validation lookup.
- **Don't write secrets into artifacts.** Run evidence and JSON summaries
  must not hold env vars, tokens, or external URLs verbatim.
- **CSV is opt-in and atomic.** A workflow declares its output path; only
  ordered, tag-stripped `[CSV]` stdout records form its schema. The producer
  writes a temporary sibling and publishes it only after success. A prior CSV
  file does not prove the current run succeeded; use current run evidence.

## When this file activates

- Add new artifact.
- Change schema of existing artifact.
- Change terminal-vs-file output discipline.
- Touch `src/tests/support/report.ts` or reporting parts of
  `src/punch/`.

## Current artifact schemas

| Artifact | Schema |
|---|---|
| `reports/state/punch-run.json` | `{ "command": "run", "tests": [str], "results": [{"test": str, "workflow": str, "exitCode": int, "passed": bool, "failure": str\|null, "csvPath": str\|null, "csvRecordCount": int}], "exitCode": int, "passed": bool, "startedAt": iso8601, "durationSeconds": number }` |
| Workflow-declared CSV | Ordered, tag-stripped `[CSV]` stdout records at the workflow-configured path; written only on successful execution |
| `reports/state/test-context.json` | `{ "created_order_ids": [str], "created_at": iso8601 }` (see `order-journey.ts` for current shape) |
| `reports/<test>.html` | Free-form self-contained HTML; structure stable enough to grep for thresholds |
| `reports/<test>.json` | k6's summary JSON, **filtered** to aggregates (counts, thresholds, durations) — never raw per-iteration data |
| `reports/logs/<service>.log` | Raw container stdout+stderr, line-ordered |

A change adding, splitting, or moving an artifact fills in a row here (Path /
Format / Producer / Produced-when / Schema / Read by / Stability /
Sensitivity, as needed) in the same PR — see
[`maintenance-matrix.md`](../../docs/ai/maintenance-matrix.md).

## Observability discipline

Punch's reference services are a didactic demo, not a production system with
on-call — so OpenTelemetry tracing, a separate metrics backend, and
symptom-based alerting are **out of scope**, deferred until a real recurring
service use case is approved. The transferable discipline that *is* in scope:

- **Instrument with a question in mind.** Before adding a log line, write 2–4
  on-call-style questions it answers (e.g. "what fraction of create-order
  requests fail, and why?"). Telemetry without a question is noise.
- **Structured logs, not prose.** One JSON object per line, stable event name +
  machine fields (`{ event: 'order_create_failed', orderId, cause, ms }`), not
  string interpolation — so it survives into `reports/logs/` and stays
  greppable next to the k6 evidence. Levels: `error` (invariant broken) ·
  `warn` (degraded) · `info` (business event) · `debug` (off by default).
- **Carry a correlation id** from the gateway through catalog/orders so one
  request can be reconstructed from interleaved logs — an orphan log line
  (no correlation id) is a red flag.
- **RED, read from the k6 run.** Rate/Errors/Duration come from the k6 summary
  (`http_req_duration` p95/p99, `errorRate`, `totalRequests`), never a separate
  metrics backend. **Percentiles, never averages.**
- **Never log secrets/tokens/URLs/PII** into any artifact — allowlist fields,
  don't dump whole request bodies.
- **Verify the telemetry itself before "done."** Force a failure (e.g. a bad
  order payload), find the structured `*_failed` event in `reports/logs/` by
  correlation id, confirm fields are real JSON (not `[object Object]`); run
  `./bin/punch run journey --collect-logs` and confirm logs line up with
  `reports/state/punch-run.json`.
- **The run evidence is the telemetry.** `reports/state/punch-run.json` answers
  "did it pass?"; `reports/logs/<service>.log` answers "why not?" — keep the split.

**Red flags:** a service path with retries/queries/external hops and zero new
log events; log lines built by string interpolation; no correlation id;
latency reported as an average; secrets/full bodies/PII in any log line.

## Build prompt

Use [`punch-build`](../prompts/punch-build.prompt.md) — `punch-builder` classifies data-harvest tasks into its runtime subsystem.

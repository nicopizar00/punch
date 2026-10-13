---
applyTo: "src/tests/support/**,src/punch/**,reports/**,docs/workflows/validation.md"
description: Contract for artifacts, logs, summaries, and reports produced by Punch.
---
# Artifacts & Reporting — Path Instructions

Scope: everything that writes under `reports/` (`src/tests/support/`, the
evidence and logging code in `src/punch/`) and
[`docs/workflows/validation.md`](../../docs/workflows/validation.md), which
holds the artifact list and the `punch-run.json` schema. Don't restate them
here.

## Rules

- **Stable paths.** Artifact paths are a public contract read by CI's
  `validate-artifacts` job. Renaming or splitting one updates every consumer
  (CI workflow, validation.md) in the same change.
- **No dates or hashes in filenames.** They break CI lookup.
- **Quiet terminal, complete files.** The terminal shows progress and
  pass/fail; full output always lands in `reports/logs/`, so a failure never
  needs a re-run for detail.
- **Compact JSON summaries.** Aggregates (counts, thresholds, durations) in a
  shape that survives k6 upgrades; no per-iteration dumps.
- **Self-contained HTML.** One file per script; no external CSS, remote
  fetches, or `file://` assumptions.
- **Evidence is canonical.** `passed` in `reports/state/punch-run.json` is the
  gate; HTML is for humans.
- **Datasets are opt-in and atomic.** Only `[DATA <dataset>]` stdout lines of
  a `--produce` dataset are written, under a header row, published only after
  success. An old data file is not evidence.
- **No secrets.** Evidence, summaries, and logs never hold tokens, env values,
  external URLs, or PII; allowlist fields instead of dumping bodies.

## Service logging

The reference services are a demo, so tracing, a metrics backend, and
alerting are out of scope. Within scope:

- Log with a question in mind ("what fraction of create-order requests fail,
  and why?").
- One JSON object per line with a stable event name and machine fields
  (`{ event: 'order_create_failed', orderId, cause, ms }`); levels `error`,
  `warn`, `info`, `debug` (off by default).
- Carry a correlation id from the gateway through catalog and orders.
- Rate, errors, and duration come from the k6 summary — percentiles, never
  averages.
- Verify by forcing a failure, then finding its `*_failed` event in
  `reports/logs/` (`./bin/punch run journey --collect-logs`).

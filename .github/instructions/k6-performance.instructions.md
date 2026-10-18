---
applyTo: "src/tests/**/*.ts,src/tests/**/*.ts.example"
description: Behavior rules for k6 tests (HTTP today, Browser deferred).
---
# k6 Performance Tests — Path Instructions

Scope: `src/tests/`.

## Rules

- **k6 doesn't orchestrate.** No containers, Compose polling, shelling out, or
  writes outside `/reports/`.
- **TypeScript ESM, one bundle per test.** No dynamic `import()`, no Node-only
  APIs at k6 runtime. Shared helpers come from `support/` or the
  `@lib` / `@utils` / `@app-types` aliases.
- **Configurable target.** Read `__ENV.TARGET_BASE_URL`; an in-network
  default (`http://gateway-api:3000`) must work with no env vars, unless the
  job lists the variable under `environment.required`. Never hardcode
  external URLs.
- **Thresholds are the gate.** Gate and journey tests declare
  `http_req_failed`, an `http_req_duration` percentile, and `checks`
  thresholds at the top of the file. Smoke tests may skip thresholds but call
  `check()`.
- **Load shape.** A test either exports its own shape (`vus`, `duration`, …,
  as the bundled tests do) or takes it from a k6 config
  (`spec.k6.config` / `--config`). A test that takes it from a config must not
  export `scenarios`, `vus`, `iterations`, `duration`, or `stages`; k6 lets
  script options win.
- **`handleSummary` writes evidence:** `/reports/<script>-report.html` (via
  `support/report.ts`) and a compact `/reports/<script>.json`. Journeys also
  write `/reports/state/test-context.json`.
- **Datasets.** A producer prints `[DATA <dataset>] <csv row>` on stdout; a
  consumer reads the file named by `DATA_<DATASET>_CSV`.
- **No secrets or real customer data.** Use fixture IDs (`prod-001`, …).
- **Explicit shared data.** `SharedArray` with a named loader; resolve `__ENV`
  in init or `setup()`, not mid-iteration.
- **Readable over clever.** Three similar `default function` bodies beat a
  metaprogrammed factory; extract a helper on its third real use.

## Browser tests (deferred)

`grafana/k6:0.55.0` in `docker/k6.Dockerfile` ships no Chromium.
`browser-smoke.ts.example` documents the intended shape and is not built; do
not add it to the esbuild entries without a plan. When enabled, browser tests
get their own bundle and Compose service, use `browser_web_vital_*`
thresholds, and keep the same `handleSummary` contract.

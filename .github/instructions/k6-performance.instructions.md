---
applyTo: "src/tests/**/*.ts,src/tests/**/*.ts.example"
description: Behavior rules for k6 tests (HTTP today, Browser deferred).
---
# k6 Performance Tests — Path Instructions

Scope: all `.ts` files under `src/tests/`. HTTP and Browser tests share this file, live in separate sub-sections.

## Shared rules (apply to all k6 tests)

- **k6 does not own orchestration.** Test must not start containers, poll compose state, shell out, or write outside `/reports/`. Those belong to `src/punch/` and `docker-compose.yml`.
- **TypeScript, ESM, single-file bundles.** esbuild makes one ES module per test. No dynamic `import()`, no Node-only APIs at k6 runtime.
- **Configurable base URL.** Every test reads target from `__ENV.TARGET_BASE_URL`. In-network default (e.g. `http://gateway-api:3000`) must work with no env vars. Never hardcode external URLs.
- **Thresholds are the gate.** Each gate or journey test declares minimum:
  - `http_req_failed` rate threshold
  - `http_req_duration` p95 threshold (or Browser equivalent below)
  - `checks` pass-rate threshold
  Smoke tests may skip thresholds but must include `check()` calls. Keep thresholds at top of file, not buried.
- **handleSummary writes evidence.** Every test exports `handleSummary`, writes self-contained HTML report (via `support/report.ts`) plus compact JSON summary under `/reports/`. Journey tests also write `reports/state/test-context.json`.
- **No secrets, no real customer data.** Use deterministic fixture IDs (`prod-001`, …). Never embed tokens, real URLs, or PII.
- **Shared helpers live in `src/tests/support/`.** Don't duplicate HTML builder. If helper needed in two tests, wait for third real caller before extracting; else inline.
- **Readable scenarios over clever abstractions.** Three similar `default
  function` bodies beat metaprogrammed factory. Reader often debugging perf regression at 2am.
- **Shared data sources are explicit.** Use `SharedArray` with named loader function. Never read `__ENV` mid-iteration; pre-resolve in `setup()`.

## HTTP test rules

Applies to `src/tests/*.ts` that are **not** `browser-*.ts`.

- File naming: `<feature>-<kind>.ts` where kind is `smoke`, `gate`, or `journey`. Bundle name in `dist/` mirrors source name.
- Use `http` module from k6 (`import http from 'k6/http'`).

## Browser test rules

Applies to `src/tests/browser-*.ts` and `src/tests/browser-*.ts.example`.

- **Currently deferred.** Reference k6 image (`grafana/k6:0.55.0`) ships no Chromium. MVP placeholder is `src/tests/browser-smoke.ts.example`; documents intended shape but is **not built** and must not be added to esbuild entry list until a Plan accepts cost.
- **No `import { browser } from 'k6/browser'` in built tests.** `.example` placeholder may contain it for docs; production tests bundled by esbuild stay HTTP-only.
- When enabled (post-Plan), Browser tests:
  - Get own bundle and own compose service.
  - Use browser-specific thresholds (`browser_web_vital_lcp`, `browser_web_vital_fid`, `browser_web_vital_cls`).
  - Reuse same `handleSummary` evidence contract as HTTP tests.

## When this file activates

- Authoring new test under `src/tests/`.
- Changing thresholds.
- Modifying `src/tests/support/report.ts`.
- Bumping `grafana/k6` pin in `docker/k6.Dockerfile`.
- Considering whether to revive Browser placeholder.

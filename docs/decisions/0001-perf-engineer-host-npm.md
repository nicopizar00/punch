# ADR 0001 — punch-builder's performance-test subsystem may use host npm

**Status:** Accepted (2026-06-17); agent surface renamed 2026-08-28 when
`punch-runtime-engineer` + `punch-performance-test-engineer` collapsed into a
single implementation-capable `punch-builder` (subsystem-scoped, not a
router). This ADR's exception now binds to punch-builder's
**performance-test subsystem work** specifically, not to the whole agent.
**Deciders:** repository owner + Punch Builder architecture work

> **Superseded in part by ADR 0005:** the historical stdlib/no-host-pip assumption
> in this ADR is superseded by the pinned PyYAML host runtime and
> explicit `requirements.txt` installation. This ADR's narrow host-`npm`/`k6`
> authoring exception for the performance-test subsystem remains accepted.

## Context

Punch Rule #1 = **Docker First**: host need only Docker + stdlib Python 3 runtime — "no Node, no k6". `npm`/esbuild run **inside** `docker/k6.Dockerfile` builder stage, never host commands.

`punch-builder`'s performance-test subsystem owns k6 test scripting **and** TypeScript/esbuild bundle toolchain — `package.json`, `tsconfig.json`, esbuild config, lint. Invoked heavy during `/punch-build`. Authoring + maintaining toolchain (dep bumps, type-check, lint, local bundle iteration) much slower if every `npm` action go through container round-trip.

## Decision

`punch-builder`, **while working the performance-test subsystem**, is the **single sanctioned surface** allowed to run host `npm`/`pnpm`/`esbuild`/lint — **and host `k6` for smoke pre-check** (`npm run smoke:local`) — while authoring + maintaining k6 test toolchain. This **scoped exception** to Docker First, not repeal:

- Exception applies **only** while `punch-builder` is working the performance-test subsystem's paths (`package.json`, `tsconfig.json`, esbuild/lint config, `src/tests/**`) and **`smoke:local` host-k6 gate** — fast *does-script-run* check before orchestration. Run **smoke only**, write **no** canonical `punch-run.json`. It does **not** apply while `punch-builder` is working the runtime subsystem — that work stays Docker-only + stdlib Python, same as before the collapse.
- **No other agent, command, or contributor workflow** gain host-Node dependence. `bin/punch`, orchestration, end-user contract stay Docker-only + stdlib Python.
- **Shipped execution chain unchanged**: source → esbuild **in `docker/k6.Dockerfile`** → k6 image → run → reports. Host `npm` = *authoring* convenience; never runtime dependency.

## Consequences

- **Positive:** faster, more maintainable k6 test-script authoring; bundle toolchain has clear owner.
- **Negative / watch:** contributor running punch-builder's performance-test subsystem locally need Node installed. Acceptable — opt-in (only when working test toolchain), no touch to Docker-First *runtime* guarantee.
- **Guardrail:** `CLAUDE.md` Rule #1 link here; `punch-ai-governance` treat any *other* host-`npm` usage as drift.

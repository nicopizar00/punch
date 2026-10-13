# ADR 0001 — Host npm for k6 test-toolchain authoring

- Status: Accepted (2026-06-17); scope restated 2026-10-13 after the agent
  roles it originally named were removed.

> **Superseded in part by ADR 0005:** the historical stdlib/no-host-pip assumption
> in this ADR is superseded by the pinned PyYAML host runtime and
> explicit `requirements.txt` installation. This ADR's narrow host-`npm`/`k6`
> authoring exception for k6 test-toolchain work remains accepted.

## Context

Rule 1 is Docker first: the host needs no Node and no k6, and `npm`/esbuild run
inside the `docker/k6.Dockerfile` builder stage. Authoring the k6 TypeScript
toolchain (`package.json`, `tsconfig.json`, esbuild config, lint, dependency
bumps, local bundle iteration) is much slower when every `npm` call needs a
container round-trip.

## Decision

While authoring or maintaining the k6 test toolchain (`package.json`,
`tsconfig.json`, esbuild/lint config, `src/tests/**`), a contributor may run
host `npm`/`pnpm`/esbuild/lint and host `k6` for the `npm run smoke:local`
pre-check. This is a scoped exception to Docker first, not a repeal:

- `smoke:local` runs smoke only and writes no `punch-run.json`; it is never
  evidence.
- Engine, Compose, and service work stay Docker-only with the pinned Python
  runtime. `bin/punch` never gains a host-Node dependency.
- The shipped chain is unchanged: source → esbuild in `docker/k6.Dockerfile` →
  k6 image → run → reports.

## Consequences

- Faster k6 toolchain authoring.
- Contributors doing that work need Node installed; nobody else does.
- Any other host `npm` or `k6` use is drift.

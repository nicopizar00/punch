---
applyTo: "**"
description: Cross-cutting architectural boundaries that apply to every change in Punch.
---
# Punch Architecture — Path Instructions

Scope: **every file**. Always-on architectural contract. Visual layer map: see [`docs/architecture/punch-boundaries.md`](../../docs/architecture/punch-boundaries.md).

## Ownership

| Layer | Owns | Lives in |
|---|---|---|
| Bash wrapper | route shell calls to Python | `bin/punch`, `bin/*` |
| Punch engine | reusable YAML loading, launch, streaming, CSV harvesting, evidence | `src/punch/**` |
| Docker Compose | services as runtime boundaries, image names, ports, env, healthchecks, volumes | `docker-compose.yml` |
| Dockerfiles | how each service built | `docker/*.Dockerfile` |
| Service code | per-service behavior (gateway / catalog / orders) | `src/services/**` |
| Consumer workload | workflow YAML plus k6 scenarios, thresholds, `handleSummary`, checks | consumer repository `workflows/**/*.yaml`, `src/tests/**` |
| Artifacts / reports | contract between runtime and downstream consumers | `reports/**` |

## Rules

- **Python owns orchestration.** Anything deciding *what to run, what order, what evidence* lives in `src/punch/`. Bash and k6 no.
- **Consumer workloads stay consumer-owned.** Consumer repositories own workflow YAML and k6 scripts. Punch owns the reusable `src/punch/` engine; this repository may carry bundled fixture workflows and examples, but they do not transfer workload ownership to Punch.
- **Docker Compose owns runtime boundaries.** Service names, ports, env vars, dependencies, volumes = contracts. Renames cascade.
- **k6 owns test behavior only.** k6 no start containers, no poll state, no write outside `/reports/`.
- **Bash thin wrapper.** bin script that branches on output or computes pass/fail belongs in Python.
- **Reporting product contract.** Artifact paths (`reports/state/punch-run.json`, HTML reports, JSON summaries, `reports/logs/`) stable. Renames or schema changes need Plan naming every consumer.
- **CI/CD external.** `.github/workflows/` consumes Punch; Punch no extend own ownership into CI. Workflow edits out of scope for Build prompts unless Plan explicitly authorizes.

## Anti-patterns to flag in Review

1. k6 test code that starts containers or shells out.
2. Bash that parses subprocess output to decide control flow.
3. compose service rename without dependents grep.
4. subprocess call from Python running k6 directly (skipping compose).
5. Build touching Python + compose + k6 in one diff without Plan that authorized integration.
6. reporting file path or schema change without maintenance-matrix update.

## When this file activates

Always. Global contract every other instruction file refines.

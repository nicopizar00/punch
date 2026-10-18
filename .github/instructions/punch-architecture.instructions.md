---
applyTo: "**"
description: Cross-cutting architectural boundaries that apply to every change in Punch.
---
# Punch Architecture — Path Instructions

Always on. Layer diagram:
[`docs/architecture/punch-boundaries.md`](../../docs/architecture/punch-boundaries.md).

## Ownership

| Layer | Owns | Lives in |
|---|---|---|
| Bash wrapper | routing shell calls to Python | `bin/punch`, `bin/*` |
| Punch engine | YAML loading, catalog, data/sizing plans, launch, streaming, dataset preflight and harvesting, evidence | `src/punch/**` |
| Docker Compose | services, image names, ports, env, healthchecks, volumes | `docker-compose.yml` |
| Dockerfiles | how each service is built | `docker/*.Dockerfile` |
| Service code | gateway / catalog / orders behavior | `src/services/**` |
| Consumer workload | job YAML, k6 scenarios, thresholds, `handleSummary`, checks | consumer `jobs/**/*.yaml`, `src/tests/**` |
| Artifacts | contract between runtime and readers | `reports/**` |

## Rules

- **Python owns orchestration.** What to run, in what order, with what
  evidence lives in `src/punch/`, never in Bash or k6.
- **Consumer workloads stay consumer-owned.** Consumer repositories own job YAML and k6 scripts. Punch owns the reusable `src/punch/` engine; this repository's bundled jobs and tests are fixtures.
- **Compose owns runtime boundaries.** Service names, ports, env vars,
  dependencies, and volumes are contracts; renames cascade.
- **k6 owns test behavior only.** No containers, no polling, no writes
  outside `/reports/`.
- **Artifact paths are stable.** Renaming or reshaping
  `reports/state/punch-run.json`, HTML reports, JSON summaries, or
  `reports/logs/` names every consumer in the same change.
- **CI is a consumer.** `.github/workflows/` consumes Punch; edit it only
  when the change asks for it.

## Flag in review

1. k6 test code that starts containers or shells out.
2. Bash that parses subprocess output to decide control flow.
3. A Compose service rename without a dependents grep.
4. Python running k6 directly instead of through Compose.
5. Engine + Compose + k6 in one diff without a stated integration reason.
6. An artifact path or schema change without updating its consumers.

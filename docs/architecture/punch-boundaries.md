# Punch — Architectural Boundaries

This document is the authoritative map of **who owns what** in Punch. Every
prompt, skill, agent, and instruction file in this repo points back here.
Most "why did this go wrong?" answers map to a boundary crossing.

## Layered ownership

```
┌──────────────────────────────────────────────────────────────┐
│ Host                  developer/CI machine                   │
│   requires:           Docker + Python 3.10+ + requirements   │
│   never installs:     Node, npm, k6 (Punch setup installs     │
│                       pinned PyYAML explicitly)              │
├──────────────────────────────────────────────────────────────┤
│ Bash wrapper          bin/punch (entrypoint), bin/test-*     │
│   responsibility:     route the shell call to Python         │
│   never owns:         orchestration logic                    │
├──────────────────────────────────────────────────────────────┤
│ Python orchestrator   src/punch/__main__.py + execution.py   │
│   owns:               argparse, workflow launch, separate    │
│                       stdout/stderr logs, CSV confirmation,  │
│                       harvesting, exit codes, evidence       │
│   never owns:         Docker semantics, k6 thresholds, the   │
│                       HTML report shape                      │
├──────────────────────────────────────────────────────────────┤
│ Docker Compose        docker-compose.yml                     │
│   owns:               services as runtime boundaries, image  │
│                       names, exposed ports, env vars,        │
│                       depends_on / healthchecks, volumes     │
│   never owns:         business logic, test code              │
├──────────────────────────────────────────────────────────────┤
│ Docker images         docker/*.Dockerfile                    │
│   owns:               how each service is built              │
│   never owns:         service composition (lives in compose) │
├──────────────────────────────────────────────────────────────┤
│ k6 test code          src/tests/*.ts                         │
│   owns:               scenarios, thresholds, handleSummary,  │
│                       check() calls                          │
│   never owns:         how the suite is launched, container   │
│                       lifecycle, artifact collection         │
├──────────────────────────────────────────────────────────────┤
│ Monitoring /          (deferred — slot reserved)             │
│ injectables           future Prometheus, OTel, fault         │
│                       injection containers attach here       │
│   owns when added:    metric collection, fault scenarios     │
│   integrates through: docker-compose.yml (new services)      │
├──────────────────────────────────────────────────────────────┤
│ Artifacts /           reports/, reports/state/, reports/logs/│
│ reporting             punch-run.json, test-context.json,     │
│                       HTML reports, k6 JSON summaries        │
│   owns:               the *contract* between the runtime and │
│                       whoever reads results (humans, CI,     │
│                       dashboards)                            │
└──────────────────────────────────────────────────────────────┘
```

## Reusable engine and consumer workloads

Consumer repositories own workflow YAML and k6 scripts. Punch owns the
reusable `src/punch/` engine, including normalized YAML loading, workflow
launch, separate stream handling, CSV harvesting, and evidence. This
repository's `workflows/k6/*.yaml` and k6 files are bundled fixtures/examples;
they demonstrate the engine and do not make Punch the owner of a consumer's
workload definitions.

## What each layer can do to the layer above and below

| Layer | May call layer below | Must not call layer above |
|---|---|---|
| Bash wrapper | Python orchestrator | — |
| Python orchestrator | one `docker compose ... run --rm` per workflow | host tools (npm, k6); installs no dependencies during `run` |
| Docker Compose | starts containers | the orchestrator |
| Docker images | install dependencies inside themselves | other services or the orchestrator |
| k6 test code | HTTP to compose-network hostnames | the orchestrator, compose, or Docker |
| Artifacts | written by k6 + orchestrator | nothing reads back into upstream layers |

## Common anti-patterns

The six-phase lifecycle (Spec → Plan → Build → Test → Review → Ship — Spec
absorbs the former Define step) is designed to catch these before merge.

1. **k6 owning orchestration.** A test file that starts containers, polls
   compose state, or writes outside `/reports/`. k6 must remain a pure load
   generator. Fix: move the logic into `src/punch/__main__.py`.
2. **Bash owning business logic.** A bin script that parses output, branches
   on it, or computes pass/fail. Bash is a thin wrapper; logic belongs in
   Python. Fix: move into `src/punch/`.
3. **Compose service names changing casually.** Renaming `gateway-api` to
   `gateway` breaks every test that does `http.get('http://gateway-api:...')`
   and every healthcheck. Service names are a contract. Fix: treat renames as
   their own Plan with a dependents grep + update.
4. **Python bypassing Compose.** A subprocess call that invokes `k6 run ...`
   directly on the host, or `docker run grafana/k6` outside the compose
   network. Punch's contract is *Docker First, Compose-mediated*. Fix:
   `docker compose run --rm k6 ...`.
5. **Build agents changing multiple layers without a Plan.** A single Build
   slice that edits Python + compose + a k6 test. Each layer has different
   reviewers and different rollback paths. Fix: stop, return to Plan, split
   the work.
6. **Reporting paths changing without a contract update.** Renaming
   `reports/state/punch-run.json` or splitting it into multiple files breaks
   the CI validation job and any downstream consumer. Fix: treat as a
   contract change — Plan must call out the cascade
   (`docs/ai/maintenance-matrix.md`).
7. **Treating stderr as CSV data.** Only workflow-declared `[CSV]` records on
   stdout are harvested. Fix: preserve separate streams in
   `src/punch/execution.py`; require confirmation and publish CSV atomically
   only after success.

## When to consult this file

- Spec phase: confirm the requested change names a single owning layer.
- Plan phase: every allowed/forbidden path in the plan should map to a layer
  here.
- Review phase: any diff that touches more than one layer must reference the
  Plan that authorized the crossing.

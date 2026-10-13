# Punch — Architectural Boundaries

Who owns what. Most "why did this go wrong?" answers map to a boundary
crossing. Components, contracts, and design decisions:
[Reference Architecture](reference-architecture-and-implementation-guide.md).

## Layered ownership

```
┌──────────────────────────────────────────────────────────────┐
│ Host                  developer/CI machine                   │
│   requires:           Docker + Python 3.10+ + requirements   │
│   never installs:     Node, npm, k6                          │
├──────────────────────────────────────────────────────────────┤
│ Bash wrapper          bin/punch, bin/*                       │
│   owns:               routing the shell call to Python       │
│   never owns:         orchestration logic                    │
├──────────────────────────────────────────────────────────────┤
│ Punch engine          src/punch/**                           │
│   owns:               CLI, workflow loading, catalog, data   │
│                       and sizing plans, compose launch,      │
│                       stream forwarding, k6 log, dataset     │
│                       preflight and harvesting, exit codes,  │
│                       evidence                               │
│   never owns:         Docker semantics, k6 thresholds, the   │
│                       HTML report shape                      │
├──────────────────────────────────────────────────────────────┤
│ Docker Compose        docker-compose.yml                     │
│   owns:               services as runtime boundaries, image  │
│                       names, ports, env vars, depends_on /   │
│                       healthchecks, volumes                  │
│   never owns:         business logic, test code              │
├──────────────────────────────────────────────────────────────┤
│ Docker images         docker/*.Dockerfile                    │
│   owns:               how each service is built              │
│   never owns:         service composition                    │
├──────────────────────────────────────────────────────────────┤
│ Reference services    src/services/**                        │
│   owns:               gateway / catalog / orders behavior    │
├──────────────────────────────────────────────────────────────┤
│ k6 test code          src/tests/**                           │
│   owns:               scenarios, thresholds, handleSummary,  │
│                       check() calls                          │
│   never owns:         launch, container lifecycle, artifact  │
│                       collection                             │
├──────────────────────────────────────────────────────────────┤
│ Artifacts             reports/, reports/state/, reports/logs/│
│   owns:               the contract between the runtime and   │
│                       whoever reads results (humans, CI)     │
└──────────────────────────────────────────────────────────────┘
```

## Reusable engine and consumer workloads

Consumer repositories own workflow YAML and k6 scripts. Punch owns the
reusable `src/punch/` engine. This repository's `workflows/k6/*.yaml` and k6
files are bundled fixtures that demonstrate the engine; they do not make Punch
the owner of a consumer's workloads.

## Allowed calls

| Layer | May call | Must not call |
|---|---|---|
| Bash wrapper | Punch engine | — |
| Punch engine | one `docker compose ... run --rm` per workflow | host `npm` or `k6`; dependency installs during `run` |
| Docker Compose | containers | the engine |
| Docker images | their own dependency installs | other services, the engine |
| k6 test code | HTTP to compose-network hostnames | the engine, Compose, Docker |
| Artifacts | written by k6 and the engine | nothing reads them back upstream |

## Anti-patterns

1. **k6 owning orchestration.** A test that starts containers, polls Compose,
   or writes outside `/reports/`. Move it to `src/punch/`.
2. **Bash owning logic.** A bin script that parses output, branches on it, or
   computes pass/fail. Move it to `src/punch/`.
3. **Casual Compose service renames.** `gateway-api` is used by tests,
   healthchecks, CI, and docs. A rename is its own change with a dependents
   grep.
4. **Python bypassing Compose.** Host `k6 run` or `docker run grafana/k6`
   outside the compose network. Use `docker compose run --rm`.
5. **One change across several layers without a reason.** Engine + Compose +
   k6 test in one diff has different reviewers and rollbacks. Split it, or
   state why the crossing is needed.
6. **Silent artifact contract changes.** Renaming or splitting
   `reports/state/punch-run.json` breaks CI validation and consumers. Name
   every consumer in the change.
7. **Treating stderr as data.** Only `[DATA <dataset>]` lines on stdout are
   harvested, only for opted-in datasets, published atomically after success
   (`src/punch/execution.py`).

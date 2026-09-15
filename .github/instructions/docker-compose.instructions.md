---
applyTo: "docker-compose.yml,docker/**"
description: Behavior rules for Docker and Docker Compose changes.
---
# Docker & Docker Compose — Path Instructions

Scope: `docker-compose.yml` and all under `docker/`. Compose own runtime boundaries; this file its contract.

## Rules

- **Services are contracts.** Service names, image names, exposed ports,
  env vars, `depends_on`, healthchecks, volume mounts = public surface. Rename any
  cascade to k6 tests, orchestrator, CI, docs (see [`docs/ai/maintenance-matrix.md`](../../docs/ai/maintenance-matrix.md)).
- **Do not mutate service names casually.** Rename = own Plan with
  dependents grep + update across `src/tests/`, `src/punch/`,
  `.github/workflows/`, docs.
- **Single compose file.** No split into multiple compose files, no
  profiles unless Plan justify.
- **Healthchecks gate startup.** Every service has healthcheck.
  `depends_on: { condition: service_healthy }` only ordering
  mechanism. No `sleep` loops in bin scripts or orchestrator.
- **No CMD in Dockerfiles for k6.** Compose `command:` single
  source of truth for how k6 invoked.
- **Multi-stage builds for Node services.** Bundling (esbuild) run in
  builder stage; runtime images carry only artifacts.
- **Pin `grafana/k6`** to explicit version. Never `:latest`.
- **No secret bake-ins.** Use env vars passed through
  compose, never `ARG`s that hardcode credentials.
- **Volumes are read-only by default.** Only `./reports:/reports`
  writable. Mounts needing write access must justify in Plan.
- **Explicit host prerequisites.** A fresh clone requires Docker, Python
  3.10+, and the pinned PyYAML requirements. Run
  `python3 -m pip install -r requirements.txt` before `./bin/punch`; do not
  add host Node or k6 requirements. `punch run` never installs dependencies.
- **Local reproducibility.** Compose run must be deterministic enough
  that two contributors get same artifact set from same commit.

## When the orchestrator changes

If `bin/punch` add new test target, this layer change too: add new
bundle to `docker/k6.Dockerfile` COPY step, ensure compose
`command:` accept script path. Coordinate via Plan.

## Service contract template

Every service in `docker-compose.yml` declares:

```yaml
services:
  <service-name>:                       # stable, lowercase, kebab-case
    image: <name>:<pinned-tag>          # never `:latest`
    container_name: <service-name>      # log clarity
    build:                              # only if local
      context: .
      dockerfile: docker/<service>.Dockerfile
    ports:
      - "<host>:<container>"            # only when host needs reachability
    environment:
      VAR_NAME: ${VAR_NAME:-default}    # every var is part of the surface
    depends_on:
      <other-service>:
        condition: service_healthy      # never service_started for deps
    healthcheck:                        # every service has one
      test: ["CMD", "<lightweight-check>"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s
    volumes:
      - ./<host-path>:<container-path>:ro   # read-only by default
    networks:
      - punch-net
```

**Contract changes** (need a Plan + dependents cascade, see
[`maintenance-matrix.md`](../../docs/ai/maintenance-matrix.md)): renaming a
service, changing a host-exposed port, removing/weakening a healthcheck,
adding a writable volume, a major-version image bump, removing/renaming an
env var another service or `src/tests/` reads.

**Within contract** (normal Build, no separate Plan beyond it): patch-level
image bumps, tightening a healthcheck interval/timeout, adding an env var
with a backwards-compatible default.

## Build prompt

Use [`punch-build`](../prompts/punch-build.prompt.md) — `punch-builder` classifies Compose tasks into its runtime subsystem.

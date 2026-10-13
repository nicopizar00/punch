---
applyTo: "docker-compose.yml,docker/**"
description: Behavior rules for Docker and Docker Compose changes.
---
# Docker & Docker Compose — Path Instructions

Scope: `docker-compose.yml` and `docker/`.

## Rules

- **Services are contracts.** Service names, image names, ports, env vars,
  `depends_on`, healthchecks, and mounts are public. A rename is its own
  change with a dependents grep across `src/tests/`, `src/punch/`,
  `.github/workflows/`, and docs.
- **One compose file, no profiles** unless a plan justifies them.
- **Healthchecks gate startup.** Every service has one;
  `depends_on: { condition: service_healthy }` is the only ordering
  mechanism. No `sleep` loops.
- **k6 image.** The Dockerfile sets `ENTRYPOINT` (`docker/entrypoint.sh` →
  `scripts/k6-wrapper.sh`) and no `CMD`; Compose `command:` and Punch's
  `run <script>` decide what runs. `grafana/k6` stays pinned, never `:latest`.
- **Bundling happens in the builder stage.** `npm run build` (esbuild) in
  `docker/k6.Dockerfile`; the runtime image copies `dist/` only. A new bundle
  means a new entry in `package.json` `scripts.build`.
- **No baked secrets.** Pass env vars through Compose; no credential `ARG`s.
- **Read-only by default.** Only `./reports:/reports` is writable; any other
  writable mount needs a stated reason.
- **Explicit host prerequisites.** Docker, Python 3.10+, and the pinned
  PyYAML requirements (`python3 -m pip install -r requirements.txt`). No host
  Node or k6. `punch run` never installs or builds.
- **Reproducible.** Two contributors on one commit get the same artifact set.

## Contract changes

Need a plan and a dependents cascade: renaming a service, changing a
host-exposed port, removing or weakening a healthcheck, adding a writable
mount, a major image bump, removing or renaming an env var something reads.

Within contract: patch image bumps, tighter healthcheck timing, a new env var
with a backward-compatible default.

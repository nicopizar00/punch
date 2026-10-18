# k6-ts-docker

Punch turns one declarative Punch job (a YAML file) into one Docker Compose
run of k6.
This repo also holds TypeScript k6 tests and a small reference app
(gateway, catalog, orders, Postgres) so the pipeline runs end to end, locally
and in GitHub Actions. Usage and flags: [README](README.md).

Long-term goal: a reusable performance gate other teams consume without owning
the pipeline internals.

## Execution chain

    TypeScript source  →  esbuild (k6.Dockerfile builder stage)  →  k6 image  →
    k6 run (one docker compose run per job)  →  reports/

The Kubernetes runtime ([`k8s/README.md`](k8s/README.md)) keeps the chain up
to the k6 image, then runs one Argo Workflows step per job with S3
artifacts instead of `docker compose run`.

Every change preserves this chain. If a change does not fit it, stop and
discuss.

## Layout

| Path | Holds |
|---|---|
| `bin/punch` | Shell shim → `python3 -m punch` |
| `bin/*` | Legacy wrappers over `punch run` / `docker compose` |
| `src/punch/` | Engine: `__main__` (CLI, evidence), `job` (strict loader), `catalog` (dataset links), `data_plan` (pickers, pure), `sizing` (pure), `execution` (compose run, streams, datasets), `menu` (terminal UI) |
| `k8s/` | Kubernetes runtime (ADR 0006): `punch-k6-run` WorkflowTemplate, `k6-run.sh` step, executor RBAC — no Python |
| `src/tests/` | k6 TypeScript; `support/report.ts` builds HTML + JSON summaries |
| `src/lib/`, `src/utils/`, `src/types/` | k6 helpers behind the `@lib` / `@utils` / `@app-types` aliases |
| `src/services/` | Reference app (Node; dependencies install inside Docker) |
| `jobs/k6/` | Bundled job YAML (fixtures; consumers own their own) |
| `tests/` | Python `unittest` suite for the engine and CI contract |
| `docker/`, `docker-compose.yml` | Images and the single compose file |
| `scripts/k6-wrapper.sh` | Container entrypoint helper |
| `environment/` | Per-service default target hosts for the menu |
| `docs/` | Architecture, decisions (ADRs), specs, validation |
| `dist/`, `reports/` | Generated, gitignored |

## Rules

1. **Docker first.** Host needs Docker, Python 3.10+, and the pinned
   `requirements.txt` (PyYAML, simple-term-menu, rich) — no Node, no k6.
   `npm`, esbuild, and `pg` run inside Docker build stages. One scoped
   exception: while authoring the k6 TypeScript toolchain, host
   `npm`/esbuild/lint and `npm run smoke:local` are allowed; they are never
   the evidence path ([ADR 0001](docs/decisions/0001-perf-engineer-host-npm.md)).
2. **Thin façade.** Punch shells out to `docker compose run --rm`; it adds no
   execution semantics, never builds images, never installs dependencies.
   Outside YAML loading and the menu, the engine is standard library only.
3. **Evidence.** A runtime change is done when `./bin/punch run <job>`
   writes `reports/state/punch-run.json` with `"passed": true`. Doc-only
   changes need diff review only. See
   [validation.md](docs/jobs/validation.md).
4. **Layer ownership.** One owning layer per change; a crossing must say why.
   See [punch-boundaries.md](docs/architecture/punch-boundaries.md).
5. **Small, reviewable steps.** Follow the agreed plan; flag anything beyond
   it.
6. **No unnecessary dependencies, no premature abstraction.** Three similar
   lines beat a clever helper.
7. **Self-describing.** Names, layout, and scripts explain themselves; no
   hidden conventions.

## Commands

```bash
python3 -m pip install -r requirements.txt
docker compose build
./bin/punch doctor
./bin/punch run smoke                     # or gate | journey | all | path.yaml
./bin/punch run all --collect-logs
./bin/punch menu [jobs-dir]
./bin/punch clean
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
```

## Notes for AI assistants

- GitHub Copilot is the primary host: `.github/copilot-instructions.md` and
  `.github/instructions/` are its config.
- `tests/test_ci_contract.py` pins phrases in ADR 0001, `punch-boundaries.md`,
  `.github/instructions/`, and the PR template; keep them when editing.
- `browser-smoke.ts.example` is a deferred placeholder; do not build it.
- When in doubt, ask.

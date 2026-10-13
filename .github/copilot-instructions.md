# GitHub Copilot — Repository Instructions (always-on)

Short on purpose. Path-specific detail lives in `.github/instructions/`.

## Critical rules

Breaking one breaks reproducibility, safety, or trust. Stop and ask before
bending.

1. **Docker first.** Host needs Docker, Python 3.10+, and the pinned
   `requirements.txt`, installed explicitly — never by `punch run`. No host
   `npm` or `k6`, except the k6-toolchain authoring exception in
   [ADR 0001](../docs/decisions/0001-perf-engineer-host-npm.md).
2. **Thin façade.** `bin/punch` → `src/punch/`. One workflow YAML = one
   `docker compose run --rm`; `punch run` never builds images. A dataset is
   written only with `--produce <dataset>`.
3. **Evidence.** Runtime-affecting → `reports/state/punch-run.json` with
   `"passed": true`; doc-only → diff review.
   [validation.md](../docs/workflows/validation.md) is canonical.
4. **Humans merge.** Stop after opening a PR. Merge, release, and tags are
   human-only.
5. **No secrets, private URLs, or internal business context** in source,
   docs, or test inputs. External base URLs come from env vars.

## Discovery boundary

VS Code Copilot Chat reads this file and `.github/instructions/`;
[`.vscode/settings.json`](../.vscode/settings.json) disables root `AGENTS.md`,
`CLAUDE.md`, `.agents/**`, and `.claude/**` for Chat. Those files may still
serve other hosts.

## Architecture

Layer ownership and anti-patterns:
[`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md)
(always on) and
[`punch-boundaries.md`](../docs/architecture/punch-boundaries.md). Not
restated here.

## Working rules

- Don't change engine, Compose, and k6 tests in one change unless it is a
  stated integration change.
- Don't bypass Compose with host `k6` or `docker run` unless asked.
- Don't change service names, artifact paths, or public commands without
  updating docs and dependents.
- Verify with `./bin/punch doctor` and `./bin/punch run …`; unit tests
  complement, never replace, a run.
- Small diffs; no drive-by cleanup of orthogonal code.
- State assumptions before non-trivial work; on conflicting spec and code,
  stop and ask. Push back on a flawed approach with its cost and an
  alternative.
- Link instead of restating guidance that lives elsewhere.
- PR descriptions copy [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md)
  literally.

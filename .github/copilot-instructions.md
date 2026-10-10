# GitHub Copilot — Repository Instructions (always-on)

Rules apply **every** Copilot session this repo. Deliberately short. Detail in
path-specific files under `.github/instructions/`.

## Critical Rules

Violate = break reproducibility, safety, or trust. Stop and ask
before bending.

1. **Docker First execution** — Docker, Python 3.10+, and the pinned `requirements.txt` are host prerequisites. Run `python3 -m pip install -r requirements.txt` explicitly; never install dependencies from `punch run`. Never propose host-side `npm` or `k6`, **except** the narrow performance-test authoring exception — host `npm`/`pnpm`/esbuild/lint, and host `k6` only for the `npm run smoke:local` pre-check, while authoring the k6 TS toolchain; off the evidence path, shipped chain unchanged ([ADR 0001](../docs/decisions/0001-perf-engineer-host-npm.md)). Always-on contract: [`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md).
2. **Python orchestration façade** — `bin/punch` is Python plus pinned PyYAML; all other orchestration logic remains standard-library based. Execution definitions live in `workflows/k6/*.yaml`; `punch run` launches one Compose run per workflow and does not build images. `src/punch/execution.py` owns launch, logs, dataset harvesting, and consumer preflight; `src/punch/catalog.py` validates `spec.data` links. A dataset is written only with `--produce <dataset>`.
3. **Validation evidence mandatory** — a change is not "done" until it meets its class's evidence bar. Runtime-affecting → `reports/state/punch-run.json` (`passed: true`). Documentation-only → diff review, no runtime run expected. Canonical evidence matrix: [`docs/workflows/validation.md`](../docs/workflows/validation.md). Artifact contract: [`artifacts-reporting.instructions.md`](instructions/artifacts-reporting.instructions.md).
4. **Humans merge.** Stop after opening a PR. Merge, release, push tags = human-only.
   *WHY:* irreversible + externally visible. PR boundary = where human judgment enters.
5. **No secrets, no private URLs, no internal business context** in source, docs, or test inputs. Use env vars for any external base URL.

## Discovery boundary (this workspace)

VS Code Copilot Chat discovers repository customizations from
`.github/instructions` and this file — [`.vscode/settings.json`](../.vscode/settings.json)
is the enforced guard. Root `AGENTS.md`, `CLAUDE.md`, `.agents/**`, and
`.claude/**` are disabled for this workspace and are not Punch Chat canon;
they may still serve other hosts untouched.

## Architecture ownership

Each layer owns one decision domain; cross-layer changes need an explicit plan.
Layers: Bash wrapper · Python orchestrator (`src/punch/**`) · Docker Compose ·
Dockerfiles · k6 tests (`src/tests/**`) · Artifacts (`reports/**`).
Full ownership table + review anti-patterns in always-on
[`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md)
(`applyTo: **`) and [`docs/architecture/punch-boundaries.md`](../docs/architecture/punch-boundaries.md)
— not restated here.

CI/CD **external** to Punch — does not own GitHub Actions workflows.

## Coding rules

- **Never modify Python orchestration, Docker Compose, and k6 tests in
  one change** unless it is explicitly planned as an integration change.
- **Never bypass Docker Compose** by running local `k6` or
  `docker run` directly unless user explicitly asks.
- **Never introduce CI/CD ownership into Punch** unless explicitly
  requested.
- **Never change service names, artifact paths, or public commands**
  without updating docs + dependents.
- **Prefer small diffs.** One scoped change at a time.
- **Prefer explicit validation commands.** Use `./bin/punch doctor` and
  `./bin/punch run …` — not ad-hoc shell.
- **Preserve DX**: low-noise terminal output plus complete logs +
  artifacts under `reports/`.

## Default verification

- Use official Punch commands when available (`./bin/punch …`).
- Use Docker Compose **through** Punch when possible.
- Unit tests only complement, not replace runtime
  contract validation.

## Engineering principles

6. **No duplicated guidance.** New instructions must not restate content
   already in another instruction file. Link instead.
7. **Surface assumptions before non-trivial work.** State them; don't silently
   fill ambiguous requirements — cheaper to correct now than after the diff.
8. **Manage confusion actively.** Inconsistency, conflicting spec/code, or an
   unclear requirement → stop, name the confusion, ask — don't guess and
   proceed.
9. **Push back when warranted.** Flag a flawed approach with a quantified
   downside and propose an alternative; accept an informed override.
10. **Enforce simplicity and scope discipline.** Fewer lines, earned
    abstractions, boring over clever; no drive-by cleanup of orthogonal code.
11. **Verify, don't assume.** "Seems right" isn't done — every change needs
    its class's evidence (`reports/state/punch-run.json`, build/lint output,
    or diff review), per Rule 3.

## PR description

Copy checklist from [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md)
literally — don't paraphrase or invent extra items. Criteria change →
update template, not this file.

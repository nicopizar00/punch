# GitHub Copilot — Repository Instructions (always-on)

Rules apply **every** Copilot session this repo. Deliberately short. Detail in
`docs/ai/operating-model.md` + path-specific files under
`.github/instructions/`.

## Critical Rules

Violate = break reproducibility, safety, or trust. Stop and ask
before bending.

1. **Docker First execution** — Docker, Python 3.10+, and the pinned `requirements.txt` are host prerequisites. Run `python3 -m pip install -r requirements.txt` explicitly; never install dependencies from `punch run`. Never propose host-side `npm` or `k6`, **except** the narrow performance-test authoring exception — host `npm`/`pnpm`/esbuild/lint, and host `k6` only for the `npm run smoke:local` pre-check, while authoring the k6 TS toolchain; off the evidence path, shipped chain unchanged ([ADR 0001](../docs/ai/decisions/0001-perf-engineer-host-npm.md)). Always-on contract: [`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md).
2. **Python orchestration façade** — `bin/punch` is Python plus pinned PyYAML; all other orchestration logic remains standard-library based. Execution definitions live in `workflows/k6/*.yaml`; `punch run` launches one Compose run per workflow and does not build images. `src/punch/execution.py` owns launch, logs, dataset harvesting, and consumer preflight; `src/punch/catalog.py` validates `spec.data` links. A dataset is written only with `--produce <dataset>`.
3. **Validation evidence mandatory** — a change is not "done" until it meets its class's evidence bar. Runtime-affecting → `reports/state/punch-run.json` (`passed: true`). Documentation/Copilot-only → diff review, no runtime run expected. Canonical evidence matrix: [`docs/workflows/validation.md`](../docs/workflows/validation.md). Artifact contract: [`artifacts-reporting.instructions.md`](instructions/artifacts-reporting.instructions.md).
4. **Human approves Ship.** Agent Mode MUST stop after opening PR. Merge, release, push tags = human-only.
   *WHY:* irreversible + externally visible. PR boundary = where human judgment enters.
5. **No secrets, no private URLs, no internal business context** in source, docs, prompts, or test inputs. Use env vars for any external base URL.

## Discovery boundary (this workspace)

VS Code Copilot Chat discovers repository customizations from
`.github/instructions` and this file — [`.vscode/settings.json`](../.vscode/settings.json)
is the enforced guard. Punch ships no prompts, agents, or skills (retired in
`a568a59`). Root `AGENTS.md`, `CLAUDE.md`, `.agents/**`, and
`.claude/**` are disabled for this workspace and are not Punch Chat canon;
they may still serve other hosts untouched.

## Architecture ownership

Each layer owns one decision domain; Build refuses cross-layer changes without
an approved Plan. Layers: Bash wrapper · Python orchestrator (`src/punch/**`) ·
Docker Compose · Dockerfiles · k6 tests (`src/tests/**`) · Artifacts (`reports/**`).
Full ownership table + Review anti-patterns in always-on
[`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md)
(`applyTo: **`) and [`docs/architecture/punch-boundaries.md`](../docs/architecture/punch-boundaries.md)
— not restated here.

CI/CD **external** to Punch — does not own GitHub Actions workflows.

## Coding rules

- **Never broad edits during Build.** Each Plan task declares
  allowed / read-only / forbidden paths. Edit only allowed paths.
- **Never modify Python orchestration, Docker Compose, and k6 tests in
  one task** unless explicitly planned as integration task with
  multiple per-layer Build steps.
- **Never bypass Docker Compose** by running local `k6` or
  `docker run` directly unless user explicitly asks.
- **Never introduce CI/CD ownership into Punch** unless explicitly
  requested. `.github/workflows/` outside Build scope by default.
- **Never change service names, artifact paths, or public commands**
  without updating docs + dependents (see [`docs/ai/maintenance-matrix.md`](../docs/ai/maintenance-matrix.md)).
- **Prefer small diffs.** One scoped task per Build step.
- **Prefer explicit validation commands.** Test uses
  `./bin/punch doctor` and `./bin/punch run …` — not ad-hoc shell.
- **Preserve DX**: low-noise terminal output plus complete logs +
  artifacts under `reports/`.

## Default verification

- Use official Punch commands when available (`./bin/punch …`).
- Use Docker Compose **through** Punch when possible.
- Unit tests only complement, not replace runtime
  contract validation.

## Engineering Principles

6. **Risk-based lifecycle.** Match phases to change risk, not a fixed count.
   Trivial, single-file, or localized fixes: Build → the change's relevant
   verification. Multi-file, behavioral, architectural, or otherwise risky
   changes: full Spec → Plan → Build → Test → Review → Ship (Spec absorbs
   former Define step). Ship always requires review + verification regardless
   of path taken to get there. Doc-only changes with no runtime-contract
   impact skip Build (straight Plan → PR).
7. **Mode discipline.** Read-only requests (audits, reviews,
   explanations) stay **Ask Mode**. Planning stays **Ask Mode**
   with Plan discipline. Edits only in **Agent Mode** within
   scoped Plan task.
8. **No duplication of AI guidance.** New instructions must not restate
   content already in `docs/ai/` or another instruction file. Link instead.
9. **Surface assumptions before non-trivial work.** State them; don't silently
   fill ambiguous requirements — cheaper to correct now than after the diff.
10. **Manage confusion actively.** Inconsistency, conflicting spec/code, or an
    unclear requirement → stop, name the confusion, ask — don't guess and
    proceed.
11. **Push back when warranted.** Not a yes-machine — flag a flawed approach
    with a quantified downside and propose an alternative; accept an informed
    override.
12. **Enforce simplicity and scope discipline.** Fewer lines, earned
    abstractions, boring over clever; touch only what the task's allowed paths
    cover — no drive-by cleanup of orthogonal code.
13. **Verify, don't assume.** "Seems right" isn't done — every change needs
    its class's evidence (`reports/state/punch-run.json`, build/lint output,
    or diff review), per Rule 3.

## Lifecycle

Spec → Plan → Build → Test → Review → Ship, scaled by Rule 6. Spec absorbs
the former Define step. Build classifies each Plan task as runtime
(Python/Compose/harvest) or performance-test (k6 + TS bundle) and stays in
that subsystem. Test is done when `reports/state/punch-run.json` proves it.
Process and gates: [`docs/ai/operating-model.md`](../docs/ai/operating-model.md).

## Change cascade (when X changes, update Y)

When change touches one area, several others usually need update in
lockstep. Full file-level cascade in
[`docs/ai/maintenance-matrix.md`](../docs/ai/maintenance-matrix.md) —
consult during Plan + Review.

## PR description

Copy checklist from [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md)
literally — don't paraphrase or invent extra items. Criteria change →
update template, not this file.

## When in doubt

Refer to `docs/ai/operating-model.md` and
instruction fragments under `.github/instructions/`. Proposing
changes touching multiple matrix rows → document verification plan
in PR description.

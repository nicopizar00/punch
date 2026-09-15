# GitHub Copilot — Repository Instructions (always-on)

Rules apply **every** Copilot session this repo. Deliberately short. Detail in
`docs/ai/operating-model.md` + path-specific files under
`.github/instructions/`.

## Critical Rules

Violate = break reproducibility, safety, or trust. Stop and ask
before bending.

1. **Docker First execution** — Docker, Python 3.10+, and the pinned `requirements.txt` are host prerequisites. Run `python3 -m pip install -r requirements.txt` explicitly; never install dependencies from `punch run`. Never propose host-side `npm` or `k6`, **except** the narrow `punch-builder` performance-test-subsystem authoring exception — host `npm`/`pnpm`/esbuild/lint, and host `k6` only for the `npm run smoke:local` pre-check, while authoring the k6 TS toolchain; off the evidence path, shipped chain unchanged ([ADR 0001](../docs/ai/decisions/0001-perf-engineer-host-npm.md)). Always-on contract: [`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md).
2. **Python orchestration façade** — `bin/punch` is Python plus pinned PyYAML; all other orchestration logic remains standard-library based. Execution definitions live in `workflows/k6/*.yaml`; `punch run` launches one Compose run per workflow and does not build images. `src/punch/execution.py` owns launch, logs, CSV confirmation, and harvesting. Interactive prompting is only for YAML-declared CSV output; CI must use `--confirm-output-data` when selecting one.
3. **Validation evidence mandatory** — a change is not "done" until it meets its class's evidence bar. Runtime-affecting → `reports/state/punch-run.json` (`passed: true`). Documentation/Copilot-only → diff review + governance parity, no runtime run expected. Canonical evidence matrix: [`docs/workflows/validation.md`](../docs/workflows/validation.md). Artifact contract: [`artifacts-reporting.instructions.md`](instructions/artifacts-reporting.instructions.md).
4. **Human approves Ship.** Agent Mode MUST stop after opening PR. Merge, release, push tags = human-only.
   *WHY:* irreversible + externally visible. PR boundary = where human judgment enters.
5. **No secrets, no private URLs, no internal business context** in source, docs, prompts, or test inputs. Use env vars for any external base URL.

## Discovery boundary (this workspace)

VS Code Copilot Chat discovers repository customizations from
`.github/instructions`, `.github/prompts`, `.github/agents`, and
`.github/skills` only — [`.vscode/settings.json`](../.vscode/settings.json) is
the enforced guard. Root `AGENTS.md`, `CLAUDE.md`, `.agents/**`, and
`.claude/**` are disabled for this workspace and are not Punch Chat canon;
they may still serve other hosts untouched.

## Architecture ownership

Each layer owns one decision domain; Build prompts refuse cross-layer without
approved Plan. Layers: Bash wrapper · Python orchestrator (`src/punch/**`) ·
Docker Compose · Dockerfiles · k6 tests (`src/tests/**`) · Artifacts (`reports/**`).
Full ownership table + Review anti-patterns in always-on
[`punch-architecture.instructions.md`](instructions/punch-architecture.instructions.md)
(`applyTo: **`) and [`docs/architecture/punch-boundaries.md`](../docs/architecture/punch-boundaries.md)
— not restated here.

CI/CD **external** to Punch — does not own GitHub Actions workflows.

## Agentic-coding rules

Custom agents bounded at runtime by shared
[`agent-guards.md`](../docs/ai/agent-guards.md) discipline (tool surface, serial
phases, scoped write approval). `punch-builder` implements Build directly —
no delegation. Review/Test/Security coordinators carry no `agent` tool either
— reference search and diff pre-scan happen inline. Ship's fan-out to those
three lives in the `punch-ship` prompt itself, not a coordinator persona.
No Punch agent currently lists sub-agents; this is today's least-privilege
configuration, not a categorical ban on ever adding delegation for a scoped
need (would go through Spec → Plan like any other change). VS Code's own
default (`chat.subagents.allowInvocationsFromSubagents: false`) already
prevents a subagent from spawning further subagents — Punch relies on that
default rather than overriding it.

- **Never broad edits during Build.** Each Build prompt declares
  allowed / read-only / forbidden paths. Edit only allowed paths.
- **Never modify Python orchestration, Docker Compose, and k6 tests in
  one task** unless explicitly planned as integration task with
  multiple per-layer Build calls.
- **Never bypass Docker Compose** by running local `k6` or
  `docker run` directly unless user explicitly asks.
- **Never introduce CI/CD ownership into Punch** unless explicitly
  requested. `.github/workflows/` outside Build scope by default.
- **Never change service names, artifact paths, or public commands**
  without updating docs + dependents (see [`docs/ai/maintenance-matrix.md`](../docs/ai/maintenance-matrix.md)).
- **Prefer small diffs.** One scoped task per Build call.
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
   of path taken to get there. Use the matching prompt in `.github/prompts/`
   for whichever phases apply. Doc-only changes with no runtime-contract
   impact skip Build (straight Plan → PR) — scoped exception, see
   [`documentation.instructions.md`](instructions/documentation.instructions.md#build-prompt).
7. **Mode discipline.** Read-only requests (audits, reviews,
   explanations) stay **Ask Mode**. Planning stays **Ask Mode**
   with Plan discipline. Edits only in **Agent Mode** within
   scoped Plan task. Phase→mode mapping:
   [`docs/ai/copilot-mode-mapping.md`](../docs/ai/copilot-mode-mapping.md).
8. **No duplication of AI guidance.** New instructions, prompts,
   skills, or agents must not restate content already in `docs/ai/` or
   another instruction file. Link instead.
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
    or a clean `punch-ai-governance` pass), per Rule 3.

(Absorbed from the retired `punch-using-agent-skills` meta-skill — its
skill-discovery decision tree lives in
[`docs/ai/skill-registry.md`](../docs/ai/skill-registry.md)'s "Skill
discovery" table; delegation-depth/roster canon lives in
[`agent-guards.md`](../docs/ai/agent-guards.md).)

## Lifecycle entry points

| Phase | Prompt | Mode | Agent |
|---|---|---|---|
| Spec     | [`punch-spec`](prompts/punch-spec.prompt.md)                   | Ask (writes spec doc)    | `punch-architect` |
| Plan     | [`punch-plan`](prompts/punch-plan.prompt.md)                   | Ask (Plan discipline)    | `punch-architect` |
| Build    | [`punch-build`](prompts/punch-build.prompt.md)                  | Agent (scoped) | `punch-builder` |
| Test     | [`punch-test`](prompts/punch-test.prompt.md)                  | Agent / Ask              | `punch-test-engineer` |
| Review   | [`punch-review`](prompts/punch-review.prompt.md)               | Ask                      | `punch-code-reviewer` |
| Ship     | [`punch-ship`](prompts/punch-ship.prompt.md)                   | Agent (gate + mechanical) | no dedicated persona — prompt's own fan-out |

Spec absorbs former Define phase (opens with clarify/refine step).
Build = single `punch-build` prompt bound to `punch-builder`, which classifies
the approved Plan task into a subsystem — runtime (Python/Compose/harvest) or
performance-test (k6 + TS bundle) — and implements it directly.
`punch-test` (TDD/Prove-It)
is the verification phase — done proven by `reports/state/punch-run.json`.

**Orthogonal maintenance phase (via `punch-ai-governance`, enforced):**
[`punch-document`](prompts/punch-document.prompt.md) — recurring documentation
reconciliation.

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

Refer to `docs/ai/operating-model.md`, `docs/ai/workflow.md`, and
instruction fragments under `.github/instructions/`. Proposing
changes touching multiple matrix rows → document verification plan
in PR description.

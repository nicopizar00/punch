# AI Operating Model

This file defines **how** AI changes flow through Punch.
For **rules** about what code may look like, see
[`.github/copilot-instructions.md`](../../.github/copilot-instructions.md) and
path-specific instructions under `.github/instructions/`.

> **Retirement note (2026-10-10).** The prompts, agent personas, skills,
> their registries, and the `.claude/` guard bridge were retired in
> `a568a59`; their leftover references were removed afterwards. The lifecycle
> below is a process any assistant or human follows — no asset binds to it.

## Foundational principle

> AI may explore broadly to understand. AI may plan structurally to
> delimit. AI may only build inside explicit scoped boundary. AI
> must verify through Punch's official runtime contract. AI must not
> expand implementation scope without returning to planning.

## The six core phases

| Phase  | Purpose                                                                                   | Mode                    | Edits allowed               |
| ------ | ----------------------------------------------------------------------------------------- | ----------------------- | --------------------------- |
| Spec   | Clarify/refine request (former Define), then translate into goals, non-goals, constraints | Ask                     | Only spec doc, if requested |
| Plan   | Produce scoped tasks with allowed / read-only / forbidden paths                           | Ask (Plan discipline)   | Only plan doc, if requested |
| Build  | Implement one approved task within declared scope                                         | Agent (scoped)          | Yes — only allowed paths    |
| Test   | Follows Build; inspects recorded RED evidence, independently reruns for GREEN             | Agent / Ask             | None                        |
| Review | Read-only critique of diff against plan                                                   | Ask                     | No                          |
| Ship   | Commit, push, open PR; **human merges**                                                   | Agent (mechanical only) | Yes (git/gh only)           |

Scale the phases to risk (copilot-instructions Rule 6): trivial, localized
fixes go Build → verification; risky changes take the full path. Build
classifies each Plan task as runtime (Python/Compose/harvest) or
performance-test (k6 + TS bundle) work and stays in that subsystem.

## AI assets

| Kind             | Lives in                                                                  | Answers                                    |
| ---------------- | ------------------------------------------------------------------------- | ------------------------------------------ |
| **Instructions** | `.github/copilot-instructions.md` (always-on) and `.github/instructions/` | "What rules apply when I touch this code?" |

Instructions are passive: loaded whenever a matching file is touched. Add a
new asset kind only through Spec → Plan, naming the gap instructions cannot
cover.

## Permission boundaries

- **No edits in Ask Mode.** If the phase says Ask, the assistant reads and
  writes prose — not files.
- **Plan output is the plan, not edits.** Build refuses to edit files outside
  the plan's allowed list.
- **Build is scoped.** Each Plan task declares allowed / read-only /
  forbidden paths. Scope expansion → stop, return to Plan.
- **Test uses official Punch commands.** No ad-hoc `docker run`, no host
  `k6` or `npm`. This does not narrow the separate performance-test authoring
  exception (CLAUDE.md Rule 1, ADR 0001). See
  [`docs/architecture/punch-boundaries.md`](../architecture/punch-boundaries.md).
- **Ship is mechanical only.** Commits, push, `gh pr create`. No merges, no
  tags, no force pushes, no skipping hooks.

## Validation gates

| Transition    | Gate                                                                                                    |
| ------------- | ------------------------------------------------------------------------------------------------------- |
| Spec → Plan   | Clear, narrowed problem statement, then goals, non-goals, acceptance criteria documented.               |
| Plan → Build  | Plan approved by human; allowed paths listed.                                                           |
| Build → Test  | Focused diff inside plan's allowed paths.                                                               |
| Test → Review | `reports/state/punch-run.json` with `passed: true` (or equivalent named artifact for non-test changes). |
| Review → Ship | Review verdict = Approve.                                                                               |
| Ship → done   | Human-merged PR.                                                                                        |

Mechanical steps: see [`../workflows/validation.md`](../workflows/validation.md).

## Where this differs from a generic agent setup

- Orchestrator is Python plus pinned PyYAML 6.0.3, simple-term-menu 1.6.6, and rich 15.0.0;
  orchestration outside YAML loading and interactive selection remains
  standard-library based. Agents make dependency installation an
  explicit host setup step and never trigger it from `punch run`.
- Consumer repositories own workflow YAML and k6 scripts. Punch owns the
  reusable `src/punch/` engine; this repository's `workflows/k6/*.yaml` files
  are bundled fixtures/examples. `src/punch/execution.py` owns one Compose
  launch per workflow, separate stdout/stderr logs, CSV confirmation, and data
  harvesting; `punch run` does not build images.
- Execution chain **strictly linear**: TS → bundle (in Docker) →
  k6 image → run → reports. Do not branch it.
- CI/CD is **external** to Punch. Punch provides reusable local/CI-compatible
  container contracts; does not own GitHub Actions workflows.

## Drift control

- Review every PR touching `.github/` or `docs/ai/` against this file and
  [`maintenance-matrix.md`](maintenance-matrix.md).
- `.github/copilot-instructions.md` is the always-on hub. If a rule moves, it
  moves there (or to a linked path-specific instruction file); this file only
  describes the lifecycle.

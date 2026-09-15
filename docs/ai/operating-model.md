# AI Operating Model

This file define **how** AI changes flow through Punch.
For **rules** about what code may look like, see
[`.github/copilot-instructions.md`](../../.github/copilot-instructions.md) and
path-specific instructions under `.github/instructions/`.

(Was `operating-protocol.md`. Rename part of redesigned
lifecycle: Spec → Plan → Build → Test → Review → Ship — Spec absorb
former Define clarify step.)

## Foundational principle

> AI may explore broadly to understand. AI may plan structurally to
> delimit. AI may only build inside explicit scoped boundary. AI
> must verify through Punch's official runtime contract. AI must not
> expand implementation scope without returning to planning.

Single sentence whole operating model shaped around.

## The six core phases

| Phase | Purpose | Mode | Edits allowed |
|---|---|---|---|
| Spec     | Clarify/refine request (former Define), then translate into goals, non-goals, constraints | Ask | Only spec doc, if requested |
| Plan     | Produce scoped tasks with allowed / read-only / forbidden paths | Ask (Plan discipline) | Only plan doc, if requested |
| Build    | Implement one approved task within declared scope | Agent (scoped) | Yes — only allowed paths |
| Test     | Follows Build (never wraps it); inspects recorded RED evidence, independently reruns for GREEN, `punch-test` verdict | Agent / Ask | None |
| Review   | Read-only critique of diff against plan | Ask | No |
| Ship     | Commit, push, open PR; **human merges** | Agent (mechanical only) | Yes (git/gh only) |

## Orthogonal maintenance prompts

| Prompt | Purpose | Owner |
|---|---|---|
| `punch-document` | Reconcile documentation debt and inherited AI artifacts in waves | `punch-ai-governance` |

Each phase and maintenance prompt has a matching entry under `.github/prompts/`.
See [`prompt-registry.md`](prompt-registry.md) for the live inventory.

## Four kinds of AI asset

Punch's AI config use four distinct artifact types. Not
interchangeable.

| Kind | Lives in | Answers | Lifetime |
|---|---|---|---|
| **Instructions** | `.github/instructions/` (path-scoped) and `.github/copilot-instructions.md` (global, always-on hub) | "What rules apply when I touch this code?" | Long — change rarely. |
| **Prompts** | `.github/prompts/` | "How do I run *this phase* of the lifecycle?" | Long — one per phase/domain. |
| **Skills** | `.github/skills/<skill>/SKILL.md` | "What does an expert in *this domain or method* always do?" | Long — domain skills (one per subsystem) + lifecycle skills (one per method). |
| **Agents** | `.github/agents/` | "Which behavioral profile fits *this phase*?" | Long — one per persona. |

Rule of thumb: **instructions** passive (loaded whenever file
touched). **Prompts** active (human runs them). **Skills**
behavioral (prompt or agent activates them). **Agents** persona
constraints (which tools, which mode, which handoff rules) — bounded at runtime
by shared [`agent-guards.md`](agent-guards.md) discipline.

## Permission boundaries

- **No edits in Ask Mode.** If phase say Ask, agent reads and writes
  prose — not files.
- **Plan output is the plan, not edits.** Plan doc itself is
  artifact. Build refuse to edit files outside plan's allowed list.
- **Build is scoped.** Each Build prompt declares allowed / read-only /
  forbidden paths. Scope expansion → stop, return to Plan.
- **Test uses official Punch commands (Test phase only).** No ad-hoc `docker
  run`. No host `k6` or `npm` — this is the independent `punch-test-engineer`
  gate; it does not narrow `punch-builder`'s separate, scoped performance-test
  subsystem authoring exception (Rule 1). See
  [`docs/architecture/punch-boundaries.md`](../architecture/punch-boundaries.md).
- **Ship is mechanical only.** Commits, push, `gh pr create`. No merges, no
  tags, no force pushes, no skipping hooks.

## Validation gates

Change cannot advance until gate met.

| Transition | Gate |
|---|---|
| Spec → Plan | Clear, narrowed problem statement (former Define gate), then goals, non-goals, acceptance criteria documented. |
| Plan → Build | Plan approved by human; allowed paths listed. |
| Build → Test | Focused diff inside plan's allowed paths. |
| Test → Review | `reports/state/punch-run.json` with `passed: true` (or equivalent named artifact for non-test changes). |
| Review → Ship | Review verdict = Approve. |
| Ship → done | Human-merged PR. |

Mechanical steps: see [`../workflows/validation.md`](../workflows/validation.md).

## Adopting this in a team

1. **Start with one phase.** Most teams under-do Plan, over-do Build.
   Begin by enforcing "no Build without Plan" for one sprint.
2. **One persona per role, not one agent per ticket.** Agents in
   `.github/agents/` (core personas, `punch-builder` implementer, on-demand
   specialists like `punch-security-auditor`, `punch-code-reviewer`, and the
   `punch-ai-governance` maintainer) reusable across all work. Resist adding
   new core persona without killing one.
3. **Promote prompts, not prose.** When you paste same
   guidance into chat repeatedly, that's missing prompt — file small PR.
4. **Audit before adding.** New skill / new agent / new prompt should answer:
   *which existing one could not absorb this?* If answer fuzzy, don't add.

## Avoiding agent sprawl

Small agent roster (core personas + `punch-builder` implementer +
`punch-security-auditor` and `punch-ai-governance` specialists). Ceiling
enforced by *function*, not *count*:

- **Domain skills** each name a unique Punch subsystem that a path
  instruction genuinely can't carry — today that's just `punch-k6-testing`
  (performance semantics). No fixed count; a 2026-08-28 sweep retired five
  domain skills whose content fit path instructions or the governance agent
  just as well (rationale: [`skill-registry.md`](skill-registry.md)).
- **Lifecycle skills** separate axis — engineering methods adapted from
  upstream `agent-skills` set (punch-spec-driven-development, punch-incremental-implementation, …).
  Not subject to a domain-skill count, but each must name unique method, avoid
  duplicating domain skill or path-instruction, and be registered when added.
  Phase prompt *activates* lifecycle skill; phase does not become one.
- Each **agent** is a **core persona** (`punch-architect` Spec+Plan,
  `punch-builder` Build — implements directly, no dispatch,
  `punch-test-engineer` Test, `punch-code-reviewer` Review), or an on-demand
  **specialist persona** (`punch-security-auditor`, `punch-ai-governance`).
  Ship (`punch-ship` prompt) carries no dedicated persona — its fan-out +
  mechanical git/gh is the prompt's own procedure under generic Agent mode,
  not a coordinator persona (avoids the "persona invoking personas"
  anti-pattern). New core persona should require killing one; specialists
  each name unique on-demand lens.
- Each **prompt** is single lifecycle phase (Build's per-subsystem scope
  lives in `punch-builder`, not extra prompts). New prompts must show why
  existing one cannot stretch.

`punch-ai-governance` agent checks new additions against this rule
during Review. Skill axes detailed in
[`skill-registry.md`](skill-registry.md).

## Where this differs from a generic agent setup

- Orchestrator is Python plus pinned PyYAML 6.0.3; all other orchestration
  remains standard-library based. Agents make dependency installation an
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

- Run `punch-ai-governance` (via Review phase) before merging any PR
  touching `.github/` or `docs/ai/`.
- Update `skill-registry.md` and `prompt-registry.md` in same PR that
  adds or removes asset.
- `.github/copilot-instructions.md` is the always-on hub. If rule moves,
  moves there (or a linked path-specific instruction file); this file only
  describes lifecycle.

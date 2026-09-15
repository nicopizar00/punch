---
name: punch-spec-driven-development
description: Creates specs before coding. Use in the Punch Spec phase when starting a feature or change and no spec exists, or when requirements are ambiguous. The method here is stack-neutral; Punch's spec shape and stack rules live in the punch-spec prompt and the path-instructions.
applies-to: lifecycle/Spec — the method the punch-spec prompt activates; not path-scoped
---

# Spec-Driven Development

## In Punch

This is the **method** the [`punch-spec`](../../prompts/punch-spec.prompt.md) prompt
activates (agent `punch-architect`). Where the generic examples below
conflict with Punch, **Punch wins**:

- **Stack:** Docker-first, Python plus pinned PyYAML orchestrator, k6 tests.
  Docker, Python 3.10+, and `requirements.txt` are explicit prerequisites;
  never add host `npm`/`k6`, and never make `punch run` install dependencies.
  See [`punch-architecture.instructions.md`](../../instructions/punch-architecture.instructions.md).
- **Spec shape:** Punch's spec is *Goal · Non-goals · Constraints · Affected layers ·
  Artifact/log/reporting implications · Acceptance criteria* (defined in `punch-spec`).
  Use that shape; the six-area template below is the generic equivalent.
- **Acceptance = evidence:** acceptance criteria must name the run record
  `reports/state/punch-run.json`, not "tests pass".
- **Clarify first:** when the request is still a vague idea, run the clarify
  step below (absorbed from the retired `punch-idea-refine`) before specifying.
- **Documentation boundary:** Spec never cleans, regenerates, or updates
  docs. A documentation gap found during Spec goes into the spec's
  Non-goals/Constraints as a queued item for `/punch-document` — not fixed
  inline.

## Overview

Write a structured specification before writing any code. The spec is the shared
source of truth between you and the human — it defines what we're building, why,
and how we'll know it's done. Code without a spec is guessing.

## When to Use

- Starting a new feature or change with no spec yet
- Requirements are ambiguous or incomplete
- The change touches more than one architectural layer
- You're about to make an architectural decision
- The task would take more than ~30 minutes to implement

**When NOT to use:** single-line fixes, typo corrections, or changes where the
requirement is unambiguous and self-contained.

## The Gated Workflow

Spec-driven development has four stages. In Punch they map onto the lifecycle:

```
SPECIFY ──→ PLAN ──→ TASKS ──→ IMPLEMENT
(Spec phase)  (Plan phase: punch-planning-and-task-breakdown)   (Build phase)
   │           │         │            │
 human       human     human        human
 reviews     reviews   reviews      reviews
```

Do not advance a stage until the current one is validated. This skill owns
**SPECIFY**; PLAN/TASKS hand off to `punch-planning-and-task-breakdown` (Plan phase),
and IMPLEMENT hands off to the Build prompts + `punch-incremental-implementation` /
`punch-test-driven-development` (absorbed later in Phase 3).

### Stage 1: Specify

**Clarify first, when the idea is still vague (absorbed from the retired
`punch-idea-refine`).** Not every request needs this — skip straight to
"Surface assumptions" below when the ask is already concrete. Run this only
when the request is a raw, unformed idea:

1. **Understand & expand.** Restate the idea as a crisp "How Might We"
   problem statement. Ask 3-5 sharpening questions, no more (who is this for;
   what does success look like; real constraints; what's been tried; why
   now). Ground it in the actual repo — `Glob`/`Grep`/`Read` for existing
   architecture and prior art, not generic product ideation.
2. **Evaluate & converge.** Cluster what resonated into 2-3 genuinely
   different directions. Stress-test each: user value, feasibility against
   Punch's stack (Docker-first, Python plus pinned PyYAML, k6 — no host
   `npm`/`k6`; requirements installation is explicit), and differentiation. Surface hidden assumptions
   explicitly — what you're betting is true, what could kill it, what you're
   choosing to ignore.
3. **Sharpen.** Produce the problem statement, recommended direction, key
   assumptions, MVP scope, and an explicit **"Not Doing" list** — the most
   valuable part; it makes trade-offs visible. This produces *intent*, not
   the spec doc itself; acceptance criteria still land in Stage 1 below once
   the idea is sharp.

Be honest, not supportive — push back on a weak idea with specificity. Don't
generate 20+ shallow variations; 5-8 considered ones beat that. Don't skip
"who is this for" or the assumption-surfacing step. This clarify pass is
conversational, not a template to fill mechanically.

**Surface assumptions immediately.** Before writing any spec content, list what
you're assuming — for Punch that means architecture and runtime, not browsers:

```
ASSUMPTIONS I'M MAKING:
1. This change touches the Python orchestrator only (not Compose or k6).
2. The evidence contract (reports/state/punch-run.json) is unchanged.
3. PyYAML remains the only declared Python runtime dependency; all other
   orchestration stays standard-library based.
4. Docker-first — no host npm/k6 is introduced, and requirements installation
   remains explicit rather than part of `punch run`.
→ Correct me now or I'll proceed with these.
```

Don't silently fill in ambiguous requirements. The spec's whole purpose is to
surface misunderstandings *before* code gets written.

**Cover these areas** (the generic six; Punch uses the `punch-spec` shape):

1. **Objective** — what we're building and why; who runs it; what success looks like.
2. **Commands** — full executable commands. In Punch these are
   `./bin/punch doctor`, `./bin/punch run smoke|gate|journey|all` — never `npm`.
3. **Affected layers** — which of bash / Python orchestrator / Compose / k6 /
   reporting the change owns and which it must not touch (the `punch-spec` field).
4. **Constraints** — what the implementation may not do (no undeclared Python
   dependency, no service renames without a cascade, no host npm/k6 tooling).
5. **Artifact / log / reporting implications** — will any artifact path or schema
   change? Will terminal noise change? (explicit, even if "none").
6. **Acceptance criteria** — the conditions Verify will assert, naming
   `reports/state/punch-run.json`.

**Reframe vague instructions as success criteria.** Translate "make it faster"
into testable conditions:

```
REQUIREMENT: "the gate is flaky in CI"
REFRAMED:
- ./bin/punch run gate passes 5/5 consecutive local runs
- http_req_failed rate threshold holds at the declared value
- reports/state/punch-run.json shows passed: true on each run
→ Are these the right targets?
```

### Stage 2: Plan · Stage 3: Tasks

Hand off to the `punch-planning-and-task-breakdown` skill (the Plan phase / `punch-plan`
prompt). It produces scoped tasks with allowed/read-only/forbidden paths.

### Stage 4: Implement

Hand off to Build: the `punch-build` prompt, dispatched by `punch-builder` to the
matching engineer, driven by `punch-incremental-implementation` and
`punch-test-driven-development`.

## Keeping the Spec Alive

- **Update when decisions change** — change the spec first, then implement.
- **Commit the spec** if persisted (e.g. `docs/architecture/specs/<topic>.md`);
  `punch-architect` writes it only when the user asks.
- **Reference the spec in the PR** — link the section each task implements.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "This is simple, I don't need a spec" | Simple tasks need fewer lines, but still need acceptance criteria. A two-line spec is fine. |
| "I'll write the spec after I code it" | That's documentation, not specification. The value is forcing clarity *before* code. |
| "The spec will slow us down" | A 15-minute spec prevents hours of rework. |
| "Requirements will change anyway" | That's why the spec is a living document. An outdated spec still beats none. |

## Red Flags

- Starting to write code with no written requirements.
- Asking "should I just start building?" before "done" is defined.
- Implementing behavior not mentioned in any spec or task.
- Making an architectural decision without recording it.

## Verification

Before advancing to Plan, confirm:

- [ ] Goal, non-goals, and acceptance criteria are written and agreed.
- [ ] Affected layers are named (from `punch-boundaries.md`).
- [ ] Artifact/log/reporting implications are explicit (even "none").
- [ ] Acceptance criteria name `reports/state/punch-run.json`.
- [ ] No host `npm`/`k6` assumption crept in; pinned PyYAML installation is an
  explicit prerequisite when runtime commands are named.

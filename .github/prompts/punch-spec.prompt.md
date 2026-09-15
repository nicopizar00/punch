---
agent: punch-architect
description: Spec — clarify and refine the request, then convert it into goals, non-goals, constraints, and acceptance criteria. Absorbs the former Define step.
---
# Punch — Spec

**Lifecycle phase:** Spec (entry phase — absorbs former Define clarify step)
**Mode:** Code read-only; may write spec doc when persisted (per agent definition)
**Owner skill:** [`punch-spec-driven-development`](../skills/punch-spec-driven-development/SKILL.md) (method,
with its clarify step — absorbed from the retired `punch-idea-refine` — activating when the idea is still vague)
+ matching domain skill/path instructions (k6 / orchestration / compose / artifacts)
**Agent:** [`punch-architect`](../agents/punch-architect.agent.md) — Spec + Plan owner.

## When to use

Have request, issue, or symptom. Need turn into spec before Plan partitions
work. Spec = entry phase: first clarifies/refines request (former Define work),
then crystallizes into contract Plan evaluated against.

## Inputs

- Request, issue, or symptom (one paragraph).
- (Optional) failing log, artifact path, branch name, or PR URL.

## What to do

**Clarify first (former Define step).** Trace execution chain
(source → bundle → image → run → reports) for request. If idea still vague,
run `punch-spec-driven-development`'s clarify step (absorbed from the retired
`punch-idea-refine`) to sharpen into a clean problem statement. Then specify:

1. Re-state goal in one sentence — concrete, testable.
2. Enumerate non-goals — what this work explicitly won't do.
3. Capture functional requirements — observable behavior change delivers.
4. Capture technical constraints — what implementation may not do
   (no undeclared Python dependency beyond pinned PyYAML, no host npm/k6,
   no Compose service renames, etc.).
5. Identify affected architectural layers (see
   [`punch-boundaries.md`](../../docs/architecture/punch-boundaries.md)).
6. Call out artifact / log / reporting implications. Any artifact path or
   schema change? Terminal noise change? Update reporting contract if so.
7. Define acceptance criteria — conditions Test will check.

## Expected output

Spec doc written to `docs/architecture/specs/<topic>.md`, following
[`spec.template.md`](../../docs/ai/templates/lifecycle/README.md). Contains:

- **Goal** — one sentence.
- **Non-goals** — bullet list.
- **Functional requirements** — what change delivers.
- **Technical constraints** — what implementation must respect.
- **Affected layers** — which boundary(ies) own this.
- **Artifact / log / reporting implications** — explicit, even if "none".
- **Acceptance criteria** — what Test will assert.

## Validation gate

Spec approved when goal, non-goals, acceptance criteria agreed. Plan = next phase.

## Edits permitted

Only the spec doc at `docs/architecture/specs/<topic>.md` (from
`spec.template.md`). No product code. This doc is later read by Plan and the
Build agents.

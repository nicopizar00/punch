---
name: punch-code-review-and-quality
description: Conducts multi-axis code review before merge. Use in the Punch Review phase to assess a verified diff across correctness, readability, architecture, security, and performance. The method is stack-neutral; Punch routes the AI-config axis to punch-ai-governance.
applies-to: lifecycle/Review — the method behind punch-review; not path-scoped
---

# Code Review and Quality

## In Punch

This is the **method** behind [`punch-review`](../../prompts/punch-review.prompt.md)
(agent `punch-code-reviewer`, read-only). Punch routes the axes:

- **AI-config axis:** when the diff touches `.github/` or `docs/ai/`, defer to
  the [`punch-ai-governance`](../../agents/punch-ai-governance.agent.md) agent
  (frontmatter, registries, boundary/handoff hygiene).
- **Security axis:** deeper guidance in [`punch-security-and-hardening`](../punch-security-and-hardening/SKILL.md).
  Punch surfaces: gateway input, secrets/env, parameterized Postgres queries in
  `orders` — there is **no web auth/XSS frontend**.
- **Performance axis:** for threshold/k6 semantics see
  [`punch-k6-testing`](../punch-k6-testing/SKILL.md).
- **Boundaries:** check the diff against the ownership map
  ([`punch-architecture.instructions.md`](../../instructions/punch-architecture.instructions.md))
  and [`scoped-build-policy.md`](../../../docs/ai/scoped-build-policy.md) — a Build
  diff that left its allowed paths is a finding.
- **Evidence:** the verification axis checks the diff's change class against
  the canonical [evidence matrix](../../../docs/workflows/validation.md) —
  Runtime-affecting needs `reports/state/punch-run.json` (`passed: true`);
  Documentation/Copilot-only needs a clean `punch-ai-governance` pass instead,
  not an absent test run treated as a gap.

## Overview

Multi-dimensional review before merge — no change merges unreviewed. **Approval
standard:** approve when the change definitely improves overall code health, even
if imperfect. Don't block because it isn't how you'd have written it.

## The Five-Axis Review

### 1. Correctness
- Does it match the spec/task? Are edge and error paths handled?
- Does `./bin/punch run` pass, and do the checks/thresholds test the *right* thing?
- Any off-by-one, race, or state inconsistency (esp. in the orchestrator's
  subprocess/exit-code handling)?

### 2. Readability & simplicity (absorbed from the retired `punch-code-simplification`)
- Names clear and consistent with project conventions?
- **Could this be fewer lines?** Are abstractions earning their complexity?
  (repo convention: three similar lines beat a premature helper.)
- Dead code, no-op shims, `// removed` comments?
- **Chesterton's Fence first.** Before flagging something as over-complex, confirm
  you understand why it exists (responsibility, callers, edge cases, the checks
  that define its behavior) — a fix suggestion that misses the reason is a
  Critical finding waiting to happen.
- **Concrete signals worth flagging:** deep nesting (3+ levels, wants guard
  clauses); long functions (50+ lines, multiple responsibilities); nested
  ternaries; generic names (`data`/`tmp`/`res`); duplicated logic (5+ lines,
  extract only after the 3rd use); wrappers adding no value; comments that
  restate the code instead of explaining *why*.
- **Verify behavior is unchanged** by any simplification the diff makes —
  `reports/state/punch-run.json` stays `passed: true` with no check/threshold
  edits; a "simplification" that required editing a check to pass changed
  behavior, it didn't simplify.
- **Red flag:** a "simplified" version that's longer or harder to follow than
  the original, or a drive-by refactor outside the task's allowed paths.

### 3. Architecture
- Respects layer ownership? (k6 doesn't start containers or shell out; bash stays a
  thin wrapper; Python owns control flow; reporting contract intact.)
- Follows existing patterns; no needless coupling or duplication.

### 4. Security
- Input validated at the gateway boundary; secrets/URLs kept out of code, logs, and
  artifacts (Critical Rule #5).
- Postgres queries in `orders` parameterized (no string concatenation).
- External/data sources treated as untrusted. (No web auth/XSS surface in Punch.)
- Deeper pass: [`punch-security-and-hardening`](../punch-security-and-hardening/SKILL.md).

### 5. Performance
- N+1 or unbounded queries (esp. `orders` → Postgres); missing pagination.
- Thresholds present and meaningful; no synchronous heavy work in a hot path.
- Deeper pass: `punch-k6-testing`. (No UI re-render concerns — Punch has no frontend.)

## Change Sizing

```
~100 lines  → good, reviewable in one sitting
~300 lines  → acceptable for one logical change
~1000 lines → too large — split it
```

Aligns with the repo's "small, reviewable steps" convention. **Separate refactoring from
behavior change** — submit them as separate tasks. For Punch, a cross-layer change
is an *integration task* = one Build call per layer (see `scoped-build-policy.md`).

## Review Process

1. **Understand context** — what does the change accomplish; which spec/task?
2. **Review tests first** — do the k6 checks/thresholds cover the change and test
   behavior, not internals?
3. **Walk the diff** through the five axes, file by file.
4. **Categorize findings** by severity so required vs optional is clear:

| Prefix | Meaning | Author action |
|--------|---------|---------------|
| *(none)* | Required change | Address before merge |
| **Critical:** | Blocks merge | Security, data loss, broken contract |
| **Nit:** | Minor/style | May ignore |
| **Optional:/Consider:** | Suggestion | Worth considering |
| **FYI** | Informational | No action |

5. **Verify the verification** — per the [evidence matrix](../../../docs/workflows/validation.md):
   Runtime-affecting diffs need `reports/state/punch-run.json` (`passed: true`);
   Documentation/Copilot-only diffs need a clean `punch-ai-governance` pass instead.

## Dead Code Hygiene

After a change, list orphaned code explicitly and **ask before deleting** — don't
silently remove things you're unsure about.

## Dependency Discipline

Before any new dependency: does the existing stack solve it? Punch uses pinned
**PyYAML 6.0.3** for workflow loading and keeps all other orchestration
standard-library based; `pg` exists only inside `orders`' image. Install the
pinned requirements explicitly, never from `punch run`; no host `npm` is part
of the runtime contract. Every dependency is a liability — default to "no".

## Honesty in Review

- **Don't rubber-stamp.** "LGTM" without evidence helps no one.
- **Don't soften real issues.** Quantify when possible ("this N+1 adds ~50ms per
  order" beats "might be slow").
- **Push back on flawed approaches** and propose alternatives; sycophancy is a
  failure mode. Accept an informed override gracefully — comment on code, not people.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "It works, that's good enough" | Working-but-unreadable/insecure code compounds into debt. |
| "I wrote it, so it's correct" | Authors are blind to their own assumptions. |
| "We'll clean it up later" | Later never comes. The review is the gate. |
| "AI-generated code is probably fine" | AI code needs more scrutiny — confident and plausible, even when wrong. |
| "The run passed, so it's good" | A green run is necessary, not sufficient — it doesn't catch architecture or boundary issues. |

## Red Flags

- Merging without review, or review that only checks if `./bin/punch run` passed.
- "LGTM" with no evidence of an actual five-axis pass.
- A Build diff that touched files outside its task's allowed paths.
- Boundary violations (k6 starting containers; bash computing pass/fail).
- Findings with no severity labels; accepting "I'll fix it later".

## Verification

After review:

- [ ] All Critical/required issues resolved (or explicitly deferred with justification).
- [ ] Boundary + scope compliance checked against the Plan and ownership map.
- [ ] Change class's [evidence matrix](../../../docs/workflows/validation.md) bar met: Runtime-affecting → `reports/state/punch-run.json` shows `passed: true`; Documentation/Copilot-only → `punch-ai-governance` ran clean.
- [ ] Verdict recorded: **Approve** or **Request changes**.

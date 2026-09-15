---
name: punch-documentation-and-adrs
description: Records decisions and documentation — the why, not just the what. Use when making an architectural decision, changing a public command/contract, or shipping a change future engineers and agents must understand. Stack-neutral; Punch conventions live in documentation.instructions.md.
applies-to: docs/**, CHANGELOG.md — invoked when a change makes a decision or alters a contract; not phase-bound
---

# Documentation and ADRs

## In Punch

Invoked when a change makes an architectural decision, alters a public contract
(commands, artifact paths/schema, service names), or ships user-facing behavior.
Punch specifics:

- Conventions live in
  [`documentation.instructions.md`](../../instructions/documentation.instructions.md);
  the file-level change cascade lives in
  [`maintenance-matrix.md`](../../../docs/ai/maintenance-matrix.md) — a contract
  change must update both the doc and every consumer.
- Record **decisions** as ADRs under `docs/decisions/`; the spec/plan artifacts
  for in-flight work live under `docs/architecture/specs/` (see `punch-spec-driven-development`).
- **Docs for agents** are first-class: `.github/copilot-instructions.md` (the
  always-on hub) and `docs/ai/**` (operating model + registries) must stay accurate.

## Overview

Document decisions, not just code. The highest-value documentation captures the
*why* — the context, constraints, and trade-offs. Code shows *what*; docs explain
*why it's built this way* and *what alternatives were rejected*.

## When to Use

- Making a significant architectural decision or choosing between approaches.
- Changing a public command (`./bin/punch …`), an artifact path/schema, or a service name.
- Shipping a change to user-facing behavior (update `CHANGELOG.md`).

**When NOT to use:** don't document obvious code; don't restate what code already says.

## Architecture Decision Records (ADRs)

Write an ADR for any decision that would be expensive to reverse — a runtime/tooling
choice, a schema design, a reporting-contract shape. Store under `docs/decisions/`
with sequential numbering. Don't delete superseded ADRs; write a new one that
references the old.

```markdown
# ADR-005: Workflow YAML runtime

## Status
Accepted

## Date
2026-06-17

## Context
`bin/punch` orchestrates Docker Compose runs from normalized YAML workflows.
Docker, Python 3.10+, and pinned PyYAML requirements are host prerequisites.

## Decision
Use PyYAML 6.0.3 as the declared host runtime dependency for workflow loading;
keep all other orchestration in the standard library (`argparse`, `subprocess`,
`pathlib`, `json`). Install requirements explicitly, never from `punch run`.

## Alternatives Considered
### A broader CLI framework (click / typer)
- Pros: ergonomic arg parsing, less boilerplate.
- Rejected: adds an unnecessary dependency beyond the pinned YAML runtime.

### A shell-only wrapper
- Rejected: control flow (exit-code propagation, evidence writing) belongs in
  Python; bash stays a thin exec wrapper.

## Consequences
- One pinned PyYAML host dependency; `argparse` covers the small command set.
- New orchestrator features remain standard-library based unless a new decision
  explicitly changes the runtime contract.
```

## Inline Documentation

Comment the **why**, not the **what**:

```python
# BAD: restates the code
count += 1  # increment count

# GOOD: explains non-obvious intent
# Stream line-by-line (not communicate()) so Docker/k6 output reaches the
# terminal live AND the log file; buffering would break the low-noise contract.
for line in proc.stdout: ...
```

Document known gotchas where they bite (e.g. "the k6 image ships no Chromium — Browser tests are deferred; see the k6-performance instruction"). Don't leave commented-out code (git has history) or stale TODOs (do it now or file a task).

## README & Changelog

- `README.md`: quick start uses `./bin/punch …` (never `npm`); keep the command
  table current.
- `CHANGELOG.md`: add an entry for shipped behavior changes (Added / Fixed / Changed),
  referencing the PR.

## Documentation for Agents

- `.github/copilot-instructions.md` — always-on rules (the hub); keep current.
- `docs/ai/**` — operating model, registries, model selection; update in the same
  PR that adds/removes an AI asset (the registries are the governance contract).
- ADRs — so agents understand *why* past decisions were made and don't re-litigate them.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "The code is self-documenting" | Code shows what, not why or which alternatives were rejected. |
| "Nobody reads docs" | Agents do. Future engineers do. Your 3-months-later self does. |
| "ADRs are overhead" | A 10-minute ADR prevents re-deciding the same thing in six months. |
| "Comments get outdated" | *Why* comments are stable; *what* comments rot — which is why you only write the former. |

## Red Flags

- An architectural decision (runtime, schema, contract) with no written rationale.
- A changed public command / artifact path with no doc + maintenance-matrix update.
- Commented-out code instead of deletion; weeks-old TODOs.
- `.github/copilot-instructions.md` / `docs/ai/**` drifted from the actual config.

## Verification

- [ ] ADRs exist (under `docs/decisions/`) for significant, hard-to-reverse decisions.
- [ ] `README.md` / `CHANGELOG.md` updated for public command or behavior changes.
- [ ] The maintenance matrix cascade is honored for any contract change.
- [ ] `.github/copilot-instructions.md` and `docs/ai/**` are current; no commented-out code left behind.

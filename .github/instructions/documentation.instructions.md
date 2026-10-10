---
applyTo: "docs/**,README.md"
description: Behavior rules for repository documentation.
---
# Documentation — Path Instructions

Scope: `README.md`, everything under `docs/`.

## Rules

- **README is a doorway, not a manual.** Explain what Punch is, shortest local
  run, link into `docs/`. Detail go elsewhere.
- **The always-on rules live in `.github/copilot-instructions.md`.** Move a
  rule there, never duplicate it.
- **No duplication.**
  - Architecture: `docs/architecture/reference-architecture-and-implementation-guide.md`
    (solution, decisions, reproduction) + `docs/architecture/punch-boundaries.md`
    (ownership layers).
  - Decisions: `docs/decisions/`.
  If you'd repeat content, link instead.
- **AI-friendly structure.** Tables for catalogs, headings for navigation,
  explicit file paths so LLM resolve references without guessing.
- **No essays.** Terse lists over paragraphs. Section past ~40 lines, consider
  split.
- **No marketing language.** Describe what project does, not how great it is.

## When adding a new doc

- Place under right subtree (`architecture/`, `ai/`, `workflows/`,
  `validation/`).
- Add one line to the `README.md` pointer section.
- Delete or merge replaced doc in same change.

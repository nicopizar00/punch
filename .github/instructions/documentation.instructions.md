---
applyTo: "docs/**,README.md"
description: Behavior rules for repository documentation.
---
# Documentation — Path Instructions

Scope: `README.md` and `docs/`.

## Where things live

| Content | Home |
|---|---|
| What Punch is, commands, flags, workflow YAML | `README.md` |
| Components, contracts, decisions, reproduction | `docs/architecture/reference-architecture-and-implementation-guide.md` |
| Layer ownership | `docs/architecture/punch-boundaries.md` |
| Evidence and artifact schema | `docs/workflows/validation.md` |
| Decisions | `docs/decisions/NNNN-<topic>.md` |
| Feature specs | `docs/specs/spec-<topic>.md` |
| Always-on AI rules | `.github/copilot-instructions.md` |

## Rules

- **Link, don't repeat.** One home per fact.
- **Terse.** Tables for catalogs, headings for navigation, explicit file
  paths. Split a section past ~40 lines.
- **No marketing.** Say what it does.
- **Prose follows code.** When docs and tested behavior disagree, fix the
  docs or make a tested contract change.
- **Retire, don't accumulate.** Delete a replaced doc in the same change;
  executed implementation plans are not kept.
- A new doc gets one line in the README docs table.

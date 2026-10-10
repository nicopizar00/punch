# Plan — <topic>

> **Template.** Copy to `docs/architecture/specs/plan-<topic>.md`. Caveman `full`.

- **Goal** (from Spec): <one sentence>

## Tasks

### <Task ID — e.g. O-01 / C-01 / K-01 / B-01 / D-01>
- **Goal** — <one sentence>
- **Allowed edit paths** — <globs>
- **Read-only context paths** — <globs>
- **Forbidden paths** — <globs; must list every layer task not own>
- **Expected diff size** — <~N lines>
- **Validation commands** — <official `./bin/punch …` commands Test run>
- **Rollback notes** — <how undo if Test fail>
- **Human checkpoint** — human approval needed before Build
- **Build prompt** — `punch-build`

## Order of execution
<usually k6 → compose → orchestrator for integration changes>

## Cross-cutting risks
<one section for risks spanning tasks>

## Rollback plan
<for whole change>

**Gate:** approved when human confirms → Build (per task ID).

# Maintenance Matrix (file-level change cascade)

Matrix map change in one area to places reviewers and agents must check/adjust. Use to populate PR checklists and guide Copilot-style agents.

1) src/tests/* (k6 TypeScript tests)
   - Test added/removed/renamed:
     - Update Docker build expectations (dist/* bundling) — ensure new test bundled during image build.
     - Update `.github/workflows/k6.yml` if test should join CI (add run step + artifact expectations).
     - Update `.github/copilot-instructions.md` & `CONTRIBUTING.md` for new test semantics if public-facing.
     - Update `reports/` expected filenames and `validate-artifacts` job list if output names differ.
     - Update artifact contract entries in `.github/instructions/artifacts-reporting.instructions.md`.

2) dist/* and build pipeline (esbuild outputs)
   - Bundle shape or output file names change:
     - Update Dockerfiles that copy `dist/` into k6 image build context (docker/k6.Dockerfile, docker-compose.yml if scripts path changes).
     - Update CI workflow artifact list + validation checks in `.github/workflows/k6.yml`.
     - Add smoke validation in `copilot-setup-steps.yml` for local contributor verification.

3) docker/*.Dockerfile, docker-compose.yml
   - Images, exposed ports, or service names change:
     - Update `.mcp.json` service entries and port references in `.github/copilot-instructions.md`.
     - Update `.github/workflows/k6.yml` job steps (service names in docker compose commands + log collection loop).
     - Update CONTRIBUTING.md run examples and README.md quick-start commands.
     - Update compose contract in `.github/instructions/docker-compose.instructions.md`.

4) src/services/* (gateway, catalog, orders)
   - API surface (paths, ports) or behavior change:
     - Update tests under src/tests/* that call those endpoints.
     - Update report parsing assumptions in src/tests/support/report.ts if response shapes change.
     - Update integration steps in CI workflow and any healthcheck commands.

5) src/services/orders/db schema (docker/postgres/init.sql)
   - Schema change:
     - Bump migration notes in CHANGELOG.md.
     - Update seed/init SQL; ensure CI uses fresh volume or includes migration step.
     - Verify order-journey test still validates created entity fields.

6) bin/punch and src/punch/* (orchestration CLI)
   - CLI flags, subcommands, or behavior change:
     - Update README quick-start commands, CONTRIBUTING.md, and .github/workflows/copilot-setup-steps.yml for new usage.
     - Keep orchestration standard-library based beyond the pinned PyYAML runtime; flag undeclared pip/npm additions in review.
     - Update streaming pattern example in `.github/instructions/python-orchestrator.instructions.md` if streaming contract evolves.
   - YAML execution, CSV, or evidence behavior changes:
     - Update `requirements.txt`, ADR 0005, CI setup, and public prerequisite commands together.
     - Update `workflows/k6/*.yaml`, `src/punch/execution.py` ownership notes, and CSV confirmation guidance together.
     - Update `punch-run.json` fields (`failure`, `csvPath`, `csvRecordCount`) and the optional CSV artifact contract together; a prior CSV is not current-run evidence.

7) reports/ shape and filenames
   - Report names or content change:
     - Update `validate-artifacts` list in `.github/workflows/k6.yml` and job summary table.
     - Update `.github/instructions/artifacts-reporting.instructions.md` and
       `docs/workflows/validation.md` with new artifact mapping.
     - Update artifact contract entries in `.github/instructions/artifacts-reporting.instructions.md` — this contract change.

8) package.json (root and src/services/orders)
   - Dependencies change or new scripts added:
     - Update Dockerfile layers that run `npm ci` in builder stage.
     - Add Dependabot ignores or exceptions (if pinned package needs manual upgrades).
     - Update .mcp.json only if new external service introduced.

9) .github/workflows/* and action usage
   - Workflow triggers, job names, or upload artifact paths change:
     - Update README docs that reference workflow semantics.
     - Keep CI workflow names stable — agents and dashboards look up by name.

10) docs/ and README.md
    - Doc pages move or rename:
      - Update cross-references in CONTRIBUTING.md and .github/copilot-setup-steps.yml.

11) .github/prompts/* (prompts)
    - Prompts added, removed, or renamed:
      - Update `docs/ai/prompt-registry.md` in same PR.
      - Update `.github/copilot-instructions.md` lifecycle table if phase prompt name changes.
      - Run `punch-ai-governance` to confirm frontmatter and registry sync.

12) .github/skills/* (skills)
    - Skills added, removed, or renamed:
      - Update `docs/ai/skill-registry.md` in same PR.
      - Update every prompt/agent file naming the skill in its "Owner skill" / "Skill activation" section.
      - Run `punch-ai-governance` to confirm references resolve.

13) .github/agents/* (agents) — new in redesigned lifecycle
    - Agents added, removed, or renamed:
      - Update `.github/copilot-instructions.md` lifecycle table (Agent column) in same PR.
      - Update every prompt file naming the agent in its "Agent" line.
      - Update `docs/ai/copilot-mode-mapping.md` if phase-to-agent mapping changes.
      - Run `punch-ai-governance` to confirm references resolve.

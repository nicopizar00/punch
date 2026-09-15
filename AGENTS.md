AGENTS.md — AI Agents guide for this repository

Project Overview

k6-ts-docker is a didactic performance testing playground that bundles TypeScript k6 tests into Docker images and runs them against a small multi-service reference app via docker compose and GitHub Actions. Host prerequisites are Docker, Python 3.10+, and the pinned `requirements.txt`.

The orchestrator is called **Punch** (`bin/punch`, `src/punch/`).

Repository Structure

- src/services/ : reference services (gateway, catalog, orders)
- src/tests/ : k6 TypeScript test sources (smoke, gate, journey)
- src/punch/  : Python + PyYAML orchestrator used by ./bin/punch
- docker/    : Dockerfiles and postgres init SQL
- .github/   : workflows, prompts, skills, instructions, agents
- dist/      : esbuild output (gitignored)
- reports/   : run artifacts and validation state

Tech Stack

- k6 (execution) — tests written in TypeScript and bundled with esbuild during Docker image build
- TypeScript/Node (build only inside Docker)
- Docker Compose for local orchestration
- Python 3 + pinned PyYAML for the thin CLI wrapper at bin/punch; all other orchestration logic is standard-library based
- Postgres 16 for orders persistence

Build & Run

- Install with `python3 -m pip install -r requirements.txt`, then use the Python CLI: `./bin/punch run smoke | gate | journey | all`
- Legacy bash helpers available in ./bin/ (build, test-*)
- CI installs the pinned runtime, runs unit tests, builds images, then runs the workflow-backed CLI

Testing & Validation

- k6 tests produce HTML + JSON reports in reports/
- Validation job downloads artifacts and verifies expected files exist (see .github/workflows/k6.yml)
- Every verification run must produce reports/state/punch-run.json

CI / Workflows

- Primary workflow: .github/workflows/k6.yml — runs on push and pull_request to the default branch and validates artifact transfer between jobs
- AI setup workflow: .github/workflows/copilot-setup-steps.yml — helps contributors and Copilot-style agents prepare the repo environment

Key Patterns and Conventions

- Docker First: do not assume Node or k6 on the host; install Punch's pinned requirements explicitly
- Execution definitions live in `workflows/k6/*.yaml`; `punch run` does not build images
- `src/punch/execution.py` owns one Compose launch per workflow, separate stdout/stderr logs, CSV confirmation, and data harvesting
- Interactive prompting is only for YAML-declared CSV output; CI must pass `--confirm-output-data` for such a workflow
- Keep the execution chain: TypeScript -> esbuild (in Docker) -> k6 image -> run -> reports

AI Operating Model

Punch uses a six-phase lifecycle for AI-assisted changes:

    Spec → Plan → Build → Test → Review → Ship

Spec absorbs the former Define phase (it opens with a clarify/refine step). Each phase maps to one prompt under .github/prompts/ and one agent persona under .github/agents/ (Ship is the exception — no dedicated persona, see below); Build is driven by a single `punch-build` prompt bound to `punch-builder`, which classifies the approved task into a subsystem (runtime, or performance-test) and implements it directly — no delegation. The lifecycle is the operating system; the agents and skills are behavioral specializations within it.

Available agents

| Agent | Persona | Phase |
|---|---|---|
| punch-architect | Spec + Plan owner — investigator; writes spec/plan docs only | Spec, Plan |
| punch-builder | Implements Build directly — classifies task into runtime or performance-test subsystem, applies that subsystem's scope | Build |
| punch-test-engineer | Test verdict owner — runs `./bin/punch` | Test |
| punch-code-reviewer | Review verdict owner — five-axis | Review |
| punch-security-auditor | Security audit — on-demand specialist | Review security axis |
| punch-ai-governance | AI-config maintainer (user-direct; never a sub-agent) | `@mention`, Document |

Ship has no dedicated persona — `punch-ship.prompt.md` itself, run under
generic Agent mode, owns the fan-out (→ GO/NO-GO + rollback) and the
mechanical commit/push/PR. This avoids a fourth coordinator persona invoking
the three Review/Test/Security specialists, which would violate the
upstream "a persona does not invoke another persona" rule.

Definitions live in .github/agents/*.agent.md.

**Delegation (depth-1).** No Punch agent currently lists sub-agents —
`punch-builder` implements directly, and Ship's fan-out lives in the
`punch-ship` prompt, not a coordinator persona. `punch-code-reviewer` /
`punch-test-engineer` / `punch-security-auditor` carry no `agent` tool
either — reference search and diff pre-scan happen inline, not via a
spawned worker. `punch-ai-governance` is user-direct
(`disable-model-invocation: true`), in no `agents:` allowlist. This is
today's least-privilege configuration, not a categorical ban on ever adding
delegation for a scoped need.

**Vendor agent-skills personas, adopted-adapted to Punch** (Punch-named, own their
verdict): `punch-code-reviewer` (← `code-reviewer`),
`punch-security-auditor` (← `security-auditor`), `punch-test-engineer` (←
`test-engineer`). Full provenance:
[`docs/ai/agent-skills-provenance.md`](docs/ai/agent-skills-provenance.md).
`web-performance-auditor` excluded (no frontend).

Available skills

Skills come in two kinds: **domain skills** (one per Punch subsystem that a
path instruction genuinely can't carry — today just `punch-k6-testing`) and
**lifecycle skills** (engineering methods adapted from upstream —
spec-driven-development, planning, incremental-implementation,
test-driven-development, debugging, code-review-and-quality, git-workflow,
docs/ADRs, security, doubt-driven, source-driven, performance-optimization).
A 2026-08-28 governance sweep retired five domain skills whose content
turned out to be always-on context or a thin wrapper a path instruction (or
the `punch-ai-governance` agent) could carry just as well — see
[`docs/ai/skill-registry.md`](docs/ai/skill-registry.md) for what moved
where. Code simplification, idea refinement, observability, and
skill-discovery meta-routing were absorbed into their phase owners and
retired as standalone skills earlier — same registry.

The authoritative register (13 skills, with a "which skill when" discovery index) is **docs/ai/skill-registry.md**; definitions live in .github/skills/<skill>/SKILL.md.

Lifecycle entry points

The phase → prompt → agent → mode mapping is tabled in .github/copilot-instructions.md and docs/ai/prompt-registry.md (7 prompts: spec, plan, build, test, review, ship, document — no separate verify prompt; `punch-test` is the Test/verification phase). `punch-document` (doc reconciliation) is the orthogonal phase, enforced to `punch-ai-governance`.

Rules for AI assistants

- Broad read before narrow write. Spec/Plan read widely; Build edits narrowly.
- No Build without Plan. Every Build call must reference an approved Plan task ID with allowed/read-only/forbidden paths.
- No scope expansion inside Build. If a Build needs to touch a file outside the task's allowed paths, stop and return to Plan.
- Verify through Punch official commands. ./bin/punch doctor and ./bin/punch run <test> are the verification contract — not host-side docker or k6.
- Review before Ship. Ship is mechanical only (git/gh); it never introduces new logic.
- Humans merge. Ship opens the PR; humans approve and merge.

Adding a new test

1. Add a TypeScript file under src/tests/
2. Ensure the file imports support/report.ts for shared reporting
3. Confirm the new test is bundled into dist/ by the Docker build
4. Add execution steps and artifact expectations to .github/workflows/k6.yml if the test is part of the CI suite

Common Pitfalls

- Editing dist/ directly: dist/ is generated by the build stage inside Docker — don't commit built bundles unless intentional
- Host-side tooling: avoid Node/k6 assumptions. Install pinned requirements before `./bin/punch`; `punch run` never installs them

Claude Code reuse (Guard bridge)

GitHub Copilot VS Code is the primary host; `.github/` is the single source of truth. When the repo is opened in Claude Code, the project-scoped `guard` skill (`.claude/skills/guard/SKILL.md`) and thin command wraps (`.claude/commands/{spec,plan,build,test,review,ship,document}.md`) **reuse** the canonical `.github/` prompts/agents/skills — they never fork, duplicate, or override them. Rules change in `.github/` (Copilot First), never only in `.claude/`. See ADR 0004.

For deeper reading

- CLAUDE.md — project rules and architectural constitution
- docs/architecture/punch-boundaries.md — layered ownership map
- docs/ai/operating-model.md — the lifecycle and asset taxonomy
- docs/ai/workflow.md — lifecycle walkthrough with a worked example
- docs/ai/scoped-build-policy.md — allowed/forbidden paths by Build domain
- docs/ai/model-selection.md — which model class for which phase

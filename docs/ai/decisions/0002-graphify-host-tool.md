# ADR 0002 — graphify as a scoped host-tool for documentation mapping

**Status:** Superseded (2026-08-28) — the vendored `.github/skills/graphify/`
skill, its `punch-ai-governance`/`punch-document`/`punch-architect`
integration points, and its ADR-sanctioned Rule-1 host-tool exception were
all retired. Rationale: graphify is a general-purpose external visualization
utility, not a Punch-specific capability, and now ships its own official
VS Code installer — Punch no longer vendors a copy or governs its use. A
user who wants it installs it independently, entirely outside Punch's
`.github/` config and this ADR's original exception. The body below is kept
verbatim as the historical record of the original decision — including
links to `docs/ai/graphify-install.md`, which was deleted as part of this
retirement and is now a dead link within the historical text; do not
recreate it.

**Status (original):** Accepted (2026-06-18)
**Deciders:** repository owner + Punch AI Governance work

## Context

Punch Rule #1 = **Docker First**: host need only Docker + stdlib Python 3 runtime. One prior exception: [ADR 0001](0001-perf-engineer-host-npm.md) (host `npm` for perf-test engineer).

Want way to manage **documentation debt** — duplicated, stale, partial, orphaned docs across `docs/`, `docs/ai/`, `.github/`, `README.md`, `AGENTS.md`. [Graphify](https://github.com/safishamsi/graphify) (PyPI `graphifyy`, host CLI `graphify`) maps corpus into knowledge graph with community detection + `query` / `path` / `explain` / `affected` traversal — useful **evidence** for spotting drift. Host CLI installed via `uv tool install graphifyy`; **not** Dockerized, not part of source → bundle → image → run → report chain.

Pristine upstream snapshot (v0.8.41) may live in `.ai-upstream/graphify/` with provenance in its `UPSTREAM.md`. Note: `.ai-upstream/` = **gitignored local staging area** (see `.ai-upstream/.gitkeep`), not version-controlled — canonical Punch adaptation lives in `.github/` (`punch-document` prompt + `punch-ai-governance` Documentation mode). Snapshot = local drift baseline; re-fetch (`uv tool install graphifyy`, re-copy installed skill) when absent.

**Leaned for Copilot plug-in (2026-06-18).** In-repo `.github/skills/punch-graphify/` skill trimmed to Punch **in-IDE subset** (build / `--update` / `--cluster-only` / query / path / explain / add+watch / hooks). Removed: remote-repo clone & cross-repo merge, media transcription (Whisper), external-DB push (Neo4j/FalkorDB, incl. credential examples), MCP server, wiki/SVG/GraphML/obsidian exports — none used by Punch. Skill therefore **authored Punch-leaned adaptation** (subject to governance checks), not verbatim upstream copy; pristine upstream stays drift baseline in `.ai-upstream/graphify/`.

## Decision

Graphify adopted as **scoped host-tool exception** to Docker First, used **only** by documentation-reconciliation workflow. Exception, not repeal:

- **Reuse, don't fork.** Punch invokes existing `/graphify` skill, consumes its **native outputs** (`graphify-out/graph.json`, `GRAPH_REPORT.md`). No custom AST/indexing skill created.
- **One governed workflow.** `/punch-document` prompt drives existing `punch-ai-governance` agent (retired in `a568a59`), which reconciles docs in **waves** (keep / merge / rewrite / archive / delete / promote). Graphify gives map; `punch-ai-governance` makes every decision.
- **Inline skill-body execution.** `punch-ai-governance` loads `/graphify`'s skill file directly and executes its Steps 1-9 procedure in its own turn — a skill is an instructions file to read and follow, not a command to dispatch or fork. It forks only the skill's own Step 3 Part B2 chunk-extraction subagents, kept **1-deep** (`chat.subagents.allowInvocationsFromSubagents` stays default — subagents cannot spawn subagents). *(Corrected 2026-07-16 — see "Inline skill-body execution adopted" below. The original claim here, that the agent "forks `/graphify` as single subagent" via a `runSubagent` tool, was never actually wireable: `punch-ai-governance.agent.md` has no `agents:` allowlist field, and `/graphify` is a skill file, not an `.agent.md` persona that could be listed in one.)*
- **Guard reworded, not removed.** `punch-ai-governance` still **never runs Punch Docker/k6 runtime or `bin/punch` suite**. Only command surface = executing `/graphify`'s skill body inline.
- **Outputs evidence, not canonical.** Everything under `graphify-out/` = audit **evidence** — never canonical docs. `CLAUDE.md`, `docs/`, registries stay authoritative; nothing promoted to canonical without governance decision. `graph.json` and `GRAPH_REPORT.md` may be committed as shared team artifacts after passing the leakage validation checklist (see **Team Sharing** below); all other `graphify-out/` contents remain gitignored.
- **No other surface.** No other agent, command, contributor workflow gains host-graphify dependence; execution chain unchanged.

## Host tooling (`ai.ingest/`)

`ai.ingest/` is host-side stdlib Python tooling — not a Copilot asset and not inside `.github/`. It is **owned by the user**; `punch-ai-governance` does not audit it. The `ai.ingest/README.md` is the authority on its schema and usage. Drift reports from `python3 ai.ingest/compare.py` are governance-awareness only, never a blocker.

Baseline asset hashes in `ai.ingest/adopt.lock.json` are `null` (seed state) until the `adopt` command (deferred) is implemented. Until then only the version axis reports live data; asset axes report `baseline-not-recorded`. This is the documented MVP state — no false drift, no silent failure.

## Consequences

- **Positive:** documentation debt gets lean, wave-based reconciliation phase backed by graph evidence — **no new skill, no new agent** (logic folds into existing `punch-ai-governance` agent + one prompt).
- **Negative / watch:** contributor running `/punch-document` locally need graphify host-installed (`uv tool install graphifyy`). Acceptable: opt-in, off evidence/execution path, doesn't touch Docker-First *runtime* guarantee.
- **Watch:** `punch-ai-governance` now holds terminal surface (scoped to graphify map subagent). Any *other* command execution by this agent — or host-graphify use outside `/punch-document` — = **drift**.
- **Guardrail:** `CLAUDE.md` Rule #1 links here; `punch-ai-governance` treats above as only sanctioned host-graphify surface.

## Team Sharing

**Status:** Accepted (2026-06-25) — extends the original Decision above.
**Deciders:** repository owner + Punch AI Governance.

### Decision

Punch opts into a **two-tier `graphify-out/` policy**: local-only state (the original
default) plus a narrow committed-artifact allowlist for team context sharing.

**Allowlist (`.gitignore` allowlist pattern `!graphify-out/<file>`):**

| File | Status | Rationale |
|---|---|---|
| `graphify-out/graph.json` | **Committed** (after validation) | Shared team query baseline |
| `graphify-out/GRAPH_REPORT.md` | **Committed** (after validation) | Human-readable audit trail |
| `.graphifyignore` | **Committed** (shared corpus filter) | Controls which files enter the shared graph; changes require `punch-ai-governance` sign-off |
| All other root `graphify-out/` contents | Gitignored (local only) | Machine-specific or noisy |
| Nested or renamed `graphify-out*` directories | Gitignored + CI-blocked | Prevents duplicate shared graphs outside the canonical root output |

`.gitignore` uses wildcard file rules (`graphify-out/*`, `**/graphify-out/*`,
`graphify-out-*/*`, `graphify-out_*/*`) plus `!` un-ignore lines for the two
canonical files rather than the former `graphify-out/` directory rule, which
would have blocked negation.

### Validation gate (required before every commit)

`punch-ai-governance` runs all six checks and confirms before committing:

1. No absolute paths (`/(Users|home)/`) in `graph.json` or `GRAPH_REPORT.md`.
2. No venv / interpreter paths (`.venv`, `.pyenv`, `site-packages`) in either file.
3. No hostname / machine-specific strings (`MacBook`, `.local`) in either file.
4. `graph.json` parses as valid JSON.
5. All node IDs in `graph.json` are relative paths (none start with `/`).
6. No raw cost / token keys (`input_tokens`, `output_tokens`, `total_cost`) in either file (6a: `graph.json`; 6b: `GRAPH_REPORT.md`). `GRAPH_REPORT.md` may contain the human-readable summary line `Token cost: N input · N output` — accepted as non-sensitive (rounded totals only, no user data). Any raw JSON key in either file is a blocker.
7. No tracked rogue Graphify output paths: only root `graphify-out/graph.json` and root `graphify-out/GRAPH_REPORT.md` may be committed. Nested `*/graphify-out/*` and renamed `graphify-out-*/*` / `graphify-out_*/*` outputs fail CI.

Full checklist text (with commands) lived in `.github/skills/punch-graphify/SKILL.md`
(Team Share section) — retired, see "Native Graphify skill supersedes the
Punch-leaned adaptation" below; the six checks above remain the live gate.

### Forbidden by default

These commands are **never invoked autonomously** and require an explicit team decision
plus `punch-ai-governance` sign-off: `graphify *install` (vscode / claude / copilot /
agents / hook), `--watch`, `--mcp`, `graphify add <url>`, cloud semantic backends
(`GEMINI_API_KEY`), Neo4j / FalkorDB push, cross-repo / remote-repo clone.

### Consequences

- **Positive:** team members on a fresh clone can run `graphify query` without
  rebuilding — shared baseline reduces orientation cost.
- **Negative / watch:** committed graph can go stale after major codebase shape
  changes. The designated updater for a structural change is responsible for
  updating and validating the shared graph before merging.
- **Graph mode is always undirected (default).** Never rebuild the shared graph with `--directed` — it changes query behavior and is incompatible with the undirected baseline. Use `--directed` only for local personal orientation.
- **Guardrail:** `punch-ai-governance` owns the validation gate on every shared-graph
  commit. No graph update merges without the six-check sign-off.

## Native install/registration — investigated, rejected (2026-07-16)

**Status:** Rejected.
**Deciders:** repository owner + Punch AI Governance work.

### Context

`/punch-document`'s graphify delegation cannot actually invoke `/graphify` mid-turn
— no tool in `punch-ai-governance`'s surface re-enters slash-command dispatch from
inside a running turn (confirmed by re-test; see `punch-document.prompt.md` history,
commit `fe1ada7`, which replaced a false "automatic delegation" claim with an
explicit stop-and-ask-the-user handoff). This ADR's original Decision section above
also claimed the gap was closed via "VS Code-native delegation... agent forks
`/graphify` as single subagent... agent gains `runSubagent`... Implemented" — that
claim is **inaccurate**: `punch-ai-governance.agent.md` has no `agents:` allowlist
field (every other subagent-forking agent in this repo has one, naming its fork
targets by name), because `/graphify` is a skill file, not an `.agent.md` persona,
and `agents:` can only list personas. The mechanism described was never actually
wireable in that shape.

Native Graphify's own `graphify install --project --platform copilot` command
(confirmed real, `graphifyy v0.8.41`) was investigated as a fix — the reasoning
being that a natively-registered project skill might give `punch-document` a real,
host-provided invocation target instead of Punch's hand-vendored copy.

### Findings (tested directly against this repo)

1. **Wrong location.** `graphify install --project --platform copilot` writes to
   `<repo>/.copilot/skills/graphify/`. Per GitHub's own docs
   (`code.visualstudio.com/docs/agent-customization/agent-skills`), `.copilot/skills/`
   is the **personal** skills path (`~/.copilot/skills`, home-directory, cross-project)
   — not a project-level directory Copilot's workspace loader reads. The real
   project-skill directory Copilot recognizes is `.github/skills/` (where
   `punch-graphify` already lives). The tool has no `--platform` option that targets
   `.github/skills/`.
2. **Already gitignored.** `.copilot/` was gitignored in this repo before this
   investigation (`.gitignore:21`, commit `785237b`). Anything written there never
   reaches teammates or CI regardless of finding (1).
3. **Destructive side effect.** `graphify install`/`graphify uninstall` locate and
   rewrite a `## graphify` heading in `.github/copilot-instructions.md` by name
   match — `graphify uninstall` deleted a **pre-existing, hand-authored** `##
   graphify` governance section from that file (not content the tool itself had
   written), collateral damage from matching purely on heading text. Restored via
   `git restore`; no lasting damage, but confirms the command is unsafe to run
   again near this repo's existing hand-authored content.

### Decision

Native install/registration is **not adopted**. `graphify *install` (all platforms)
stays on the Forbidden-by-default list (see Team Sharing above) — this finding adds
concrete evidence (destructive rewrite of hand-authored content) to the original
"always-on injection" rationale, not just a missed opportunity.

`/punch-document`'s stop-and-ask-user handoff (`fe1ada7`) is superseded the same day
— see "Inline skill-body execution adopted" below. A second alternative (a new
`.agent.md` persona for `/graphify`) was considered and deferred, not pursued.

### Consequences

- **Positive:** avoids landing a false "delegation works now" claim in this ADR;
  avoids a destructive command near hand-authored config.
- **Negative / watch:** the mid-turn dispatch gap remains open pending the fix
  below — flagged here rather than left implicit.

## Inline skill-body execution adopted (2026-07-16)

**Status:** Accepted — supersedes `fe1ada7`'s stop-and-ask-user handoff.
**Deciders:** repository owner + Punch AI Governance work.

### Decision

`/punch-document` (via `punch-ai-governance`) reads
`.github/skills/punch-graphify/SKILL.md` (retired — see "Native Graphify skill
supersedes the Punch-leaned adaptation" below; link removed, the path no
longer exists) directly and executes its Steps 1-9 procedure inline, in its
own turn, instead of
attempting to dispatch `/graphify` as a slash command (confirmed unreachable
mid-turn, `fe1ada7`) or forking it as a subagent (never actually wireable — see
correction above). A skill is a markdown instructions file; any agent with
`search/codebase` access can open it and follow the procedure it describes,
using its own already-declared tools (`execute/runInTerminal` for the bash
blocks, `edit/editFiles` for writes, `agent` for Step 3 Part B2's chunk-extraction
subagent fan-out — the one permitted level of forking, 1-deep).

This is lower-risk than the rejected native-install path (see above): it depends
only on ordinary file-read + already-declared tool use, not on an undocumented or
misapplied platform mechanism.

### First real-world test (2026-07-16) — inconclusive, secondary gap found

Ran `/punch-document` live in GitHub Copilot / VS Code on a lifecycle-narrative
drift wave (README.md / operating-model.md vs the actual prompt/agent inventory).
Transcript showed **zero graphify usage of any kind** — no read of
`punch-graphify/SKILL.md`, no Steps 1-9, no `query`/`path`/`explain` call, no
`graphify-out/` reference. The agent reconciled the wave with plain `Read` +
grep over `.github/prompts/`, `.github/agents/`, and the registries instead.

This does **not** verify or falsify the inline-execution fix — the run never
reached the "update or full regenerate" branch of the Decision rule
(`punch-document.prompt.md:133-143`) that the fix lives in, so it was never
exercised either way. Re-test needed with a forced trigger (delete
`graphify-out/graph.json`, or a wave that clearly needs `--update`).

Separately notable: this wave — cross-referencing prompt-registry claims against
actual `.github/prompts/*` files — is exactly the kind of orphan/drift signal
Graphify's community detection exists to surface, yet the query path wasn't
consulted either, not just the build path. Open question, not yet root-caused:
is the Decision rule's "graph unnecessary" threshold too permissive, or is
`punch-context-engineering`'s gate not being reached before reconciliation work
starts? Flagged for a follow-up wave — do not assume the inline-execution fix
is the only gap left.

### Consequences

- **Positive:** closes the mid-turn dispatch gap without a human-handoff detour;
  no new files, no ADR-reopening install decision.
- **Unresolved:** neither confirmed working nor confirmed broken as of
  2026-07-16 — see test note above. A real build/update-triggering run is still
  needed before this section's "Accepted" status can be called verified.
- **Negative / watch:** unverified in live use until a real build/update wave
  runs through it. If the model still substitutes a bare CLI call despite the
  explicit "not a command to dispatch" framing, the gap reopens and the
  stop-and-ask-user handoff should be restored rather than re-attempted with
  stronger wording alone (that already failed once, see `fe1ada7`).

## Query-only contract formalized — explicit vs automatic write-back (2026-07-17)

**Status:** Accepted.
**Deciders:** repository owner + Punch AI Governance work.

### Context

`punch-context-engineering`'s Graphify gate and the explicit `/graphify
query|path|explain` commands both ran through the same
`references/query.md` procedure, which unconditionally wrote
`graphify-out/.vocab.txt` and called `graphify save-result`. Nothing in that
shared procedure distinguished a human-invoked query from Context
Engineering's automatic orientation gate — the "read-only, only" gate could
inherit both write side effects by omission, contradicting its own charter
(`SKILL.md` Graphify gate intro) and giving `graphify-out/**` a mutation path
outside `/punch-document`'s sole-writer guarantee (Team Sharing, above).

### Decision

`.github/skills/punch-graphify/SKILL.md` and `references/query.md` now define
one **query-only contract**, shared by two callers, plus the pre-existing
**maintenance profile** (`/punch-document`, sole build/`--update`/
`--cluster-only`/commit owner — unchanged by this decision):

- **Explicit profile** — a human-initiated query: `/graphify query|path|explain`
  directly, or `/punch-document`'s own query-branch (Decision rule, "Query").
- **Automatic orientation profile** — `punch-context-engineering`'s Graphify
  gate, deciding only whether a query is useful.

Both run the same vocabulary-expansion (Step 0) and traversal. Two changes
close the gap:

- Vocabulary expansion no longer persists `graphify-out/.vocab.txt` — it
  prints the token list inline and consumes it in the same turn. Vocabulary
  expansion and traversal never touch disk under `graphify-out/**`, for
  either caller.
- `save-result` write-back now runs for the **explicit profile only** —
  it *does* write under `graphify-out/**` (the mechanism the next `--update`
  reads to extract the Q&A as a graph node), but only when a human directly
  triggered the query (`/graphify query|path|explain`, or `/punch-document`'s
  own query-branch). The **automatic** orientation profile stops at the
  answer — it never calls `save-result` and never writes anything under
  `graphify-out/**`, matching its existing "never writes Graphify state"
  charter in practice, not just in prose.

`punch-context-engineering`'s gate was reworded to invoke `punch-graphify`'s
`SKILL.md#query-only-contract` by reference instead of narrating `graphify
query` mechanics inline, and `punch-document.prompt.md`'s Decision rule now
links the same contract on its Query bullet — closing the duplication gap
this ADR's original Decision section warned about ("Reuse, don't fork").

### Consequences

- **Positive:** the automatic orientation profile can no longer mutate
  `graphify-out/**` even by oversight — the write path requires the explicit
  caller by construction, not by convention alone. One canonical procedure
  removes the risk of the two call sites drifting apart on vocabulary-
  expansion behavior.
- **Negative / watch:** three call sites (`punch-graphify`'s contract,
  `punch-context-engineering`'s gate, `punch-document`'s decision rule) must
  stay cross-linked rather than re-diverging; `punch-ai-governance` checks
  this on every audit pass (duplication + cross-reference checks).

## Installation guidance corrected to the pinned version (2026-07-17)

**Status:** Accepted — clarification, not a new decision.

Current install guidance across `punch-graphify/SKILL.md` (Step 1),
`punch-context-engineering/SKILL.md` (both the gate's own install note and
its "Graphify Team Setup" user-facing message), and `punch-document.prompt.md`
Pre-conditions now uniformly reads `uv tool install graphifyy==0.8.41` — the
pin `punch-graphify/SKILL.md` Step 1 already used, so nothing here changes
which version is installed. The unpinned `uv tool install graphifyy` in this
ADR's original Context (2026-06-18) and first Consequences section above
predates the pin decision and is left as written — historical record, not
current guidance. `punch-graphify/SKILL.md` no longer exists (retired, see
"Native Graphify skill supersedes the Punch-leaned adaptation" below);
consult `docs/ai/graphify-install.md` (since retired) for the live
pin instead.

## Native Graphify skill supersedes the Punch-leaned adaptation (2026-07-17)

**Status:** Accepted — supersedes the Punch-leaned-adaptation model above
(Decision, Team Sharing, Query-only contract sections) and the native-install
rejection above. Historical findings above are left as written; this section
records the new decision.

### Context

A separate Plan (`docs/specs/plan-native-graphify-copilot.md`) replaced the
large Punch-authored `punch-graphify` adaptation with the native Graphify
Agent Skill, committed unmodified under `.github/skills/graphify/` (only
`user-invocable: true` / `disable-model-invocation: true` frontmatter added).
The prior rationale for a "leaned fork" — trimming upstream features for a
Copilot-plug-in-ready footprint — no longer applies: the native skill ships
its full upstream body, including the previously-removed reference files
(`add-watch.md`, `exports.md`, `github-and-merge.md`, `hooks.md`,
`transcribe.md`).

### Decision

- **`punch-graphify` deleted.** `.github/skills/graphify/` (native, adopted
  upstream) is the only committed Graphify skill body.
- **All custom execution paths removed.** `punch-document`,
  `punch-context-engineering`, `punch-ai-governance`, and `punch-init` no
  longer read a skill file inline, execute Steps 1-9 procedures, fork
  chunk-extraction subagents, or gate query vs. build/update/regenerate
  decisions. `/graphify` is invoked directly and explicitly by a human (or an
  agent the human is driving), exactly like any other native Copilot skill —
  no Punch wrapper stands between the user and the skill.
- **Sole Graphify write-rights model retired.** `punch-ai-governance` no
  longer claims exclusive build/update/regenerate rights over
  `graphify-out/`; there is no Punch-side write gate to hold, because no
  Punch prompt or agent writes Graphify state at all anymore. Anyone with the
  CLI installed may run `/graphify . --update` (routine) or `/graphify .`
  (structural) directly, per the upstream skill's own documented behavior.
- **Query-only contract retired as Punch-authored text.** The vocabulary-
  expansion / explicit-vs-automatic write-back split documented above
  (Query-only contract formalized) described `punch-graphify`'s adapted
  `references/query.md`; the native skill's own `references/query.md` (now
  committed verbatim) is authoritative instead. Punch no longer maintains a
  parallel copy of that procedure.
- **Team Share validation moves out of this ADR's ownership chain.** The
  committed-artifact allowlist (`graphify-out/graph.json`,
  `graphify-out/GRAPH_REPORT.md`, `.graphifyignore`) and its six-check
  validation gate are unchanged in substance but are now documented in
  `docs/ai/graphify-install.md` (installation, security, and sharing policy)
  rather than gated behind `/punch-document`'s prior sole-writer role.
- **Manual installation unchanged.** `uv tool install graphifyy` (pinned
  version tracked in `.github/skills/graphify/.graphify_version`) remains the
  required manual step; no Punch prompt runs `graphify *install` or any
  auto-install command, consistent with every prior decision in this ADR.

### Consequences

- **Positive:** removes a large, hard-to-verify custom orchestration layer
  (inline Steps 1-9 execution, chunk-extraction subagent forking, the
  query/incremental-update/full-regenerate decision rule) in favor of the
  upstream-maintained skill's own documented behavior. `punch-ai-governance`
  loses its terminal/forking exception entirely — it is now a pure
  read/edit maintainer with no scoped host-tool carve-out to audit.
- **Negative / watch:** the 2026-07-16 "Inline skill-body execution adopted"
  and 2026-07-17 "Query-only contract formalized" sections above describe a
  model that no longer runs — they are left unmodified as historical record
  per this repo's append-don't-rewrite convention for ADR history, not
  because they still reflect current behavior.
- **Guardrail:** `CLAUDE.md` Rule #1's exception (b) now reads on the
  native skill's manual-install/maintenance policy, not on any Punch-executed
  procedure; `punch-ai-governance` treats this section as the current
  sanctioned Graphify surface.

## Graphify refreshed to v0.9.30 (2026-07-30)

**Status:** Accepted — clarification, not a new decision. Supersedes only the
version number in "Installation guidance corrected to the pinned version"
above; the native-skill-only model from "Native Graphify skill supersedes..."
is unchanged.

The native Graphify Agent Skill under `.github/skills/graphify/` was refreshed
from stable `v0.9.30` (commit `ecfcd160d56b420eb8241430fa7b5b1951c7829f`,
verified against the GitHub API), replacing the prior `0.8.41` pin —
`SKILL.md` plus all `references/**`, source-identical to the upstream VS Code
variant except the two approved frontmatter fields. Current install guidance
and the live version pin are documented in
`docs/ai/graphify-install.md` (since retired), not here — this ADR
records only the tooling decision, not the live version number. No behavior,
scope, or governance change accompanies this refresh.

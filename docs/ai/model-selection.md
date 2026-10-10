# Model Selection

Guide model-agnostic, tool-agnostic. No vendor model names pinned. Use to decide *which class* of model fit *which phase* — adapt to what team has.

## Match the model to the phase

| Phase | Cognitive load | Suggested model class |
|---|---|---|
| Spec     | High — clarify request (former Define), then turn ambiguity into constraints | Strongest reasoning model |
| Plan     | High — split work, predict risks | Strongest reasoning model |
| Build    | Medium — execute within defined scope | Strong coding model |
| Verify   | Low — run commands, read results | Fast / cheap model fine |
| Review   | High — adversarial reading | Strongest reasoning model |
| Ship     | Low — mechanical git/gh actions | Fast / cheap model fine |

## Why bias toward reasoning models in Spec / Plan / Review

These phases drive every downstream decision. Cheap out here = false economy — bad Plan multiplies Build cost. Money saved on weak reasoning model paid back many times in Build / Verify cycles.

## Why a coding-capable model for Build

Build need precision over allowed paths, awareness of surrounding file style, reliable diff editing. Reasoning model weak at code-editing make diffs that pass review but fail Verify.

## Why a fast / cheap model is fine for Verify and Ship

Verify mostly:

- Runs commands.
- Reads exit codes and JSON.
- Reports pass/fail.

Ship purely:

- `git add`, `git commit`, `git push`, `gh pr create`.

Neither need long reasoning chain. Save budget for planning phases.

## Keep the model stable within a phase

Mid-phase model swap cause subtle drift:

- Two models make two different Plans for same Spec.
- Two models pick different abstractions in Build, making diff inconsistent with self.

Rule: **lock the model for the duration of one phase**. Swap only at phase boundaries (document swap in PR description if it matters).

## Don't mix models inside one implementation task

"Task" = one Plan → one Build → one Verify cycle. Treat trio as unit. Mixing models inside task tends to:

- Break naming conventions across slices.
- Re-litigate decisions previous slice settled.
- Make Review flag "internally inconsistent diff" that really "two-model fingerprint".

## Cost shape (rule of thumb)

Across complete lifecycle (Spec → Ship) on real task:

- ~60% of *cognitive* effort in Spec + Plan + Review.
- ~30% in Build (often spread across multiple Build calls).
- ~10% in Verify + Ship.

Budget that follow this distribution give most reliable outcomes. Inverting it (cheap planner, expensive shipper) = most common anti-pattern.

## Worked example

Small Python orchestrator task:

- Spec / Plan / Review: strong reasoning model.
- Build (runtime subsystem): strong coding model.
- Verify (`./bin/punch doctor`, `./bin/punch run smoke`, read JSON):
  fast model.
- Ship (commit, push, PR): fast model.

Multi-task integration (e.g. new test + new compose service + new orchestrator subcommand):

- Spec / Plan: strong reasoning model.
- Build O-01, Build C-01, Build K-01: strong coding model, **same model
  across all three Build calls** to avoid cross-slice drift.
- Verify: fast model.
- Review: strong reasoning model.
- Ship: fast model.

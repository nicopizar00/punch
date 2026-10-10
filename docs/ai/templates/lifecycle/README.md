# Lifecycle artifact templates 🧩

Blank, schema-faithful skeletons for each Punch lifecycle phase. Copy one, fill,
save to canonical location. The phase rules live in [`../../operating-model.md`](../../operating-model.md);
these are convenience scaffolds (no rule restated, only output shape).

| Phase | Template | Canonical save location | Caveman |
|---|---|---|---|
| Spec   | `spec.template.md`   | `docs/architecture/specs/<topic>.md`        | `lite` |
| Plan   | `plan.template.md`   | `docs/architecture/specs/plan-<topic>.md`   | `full` |
| Build  | `build.template.md`  | chat / PR (canon example → golden-lifecycle) | `ultra` (engineers `wenyan-lite`) |
| Test   | `test.template.md`   | chat / PR (evidence under `reports/`)        | `ultra`; evidence verbatim |
| Review | `review.template.md` | chat / PR                                    | `full` |
| Ship   | `ship.template.md`   | PR description                               | `full` |

- **Filled, real worked example** (health-smoke golden path) lives at
  [`../../golden-lifecycle/`](../../golden-lifecycle/README.md).
- These templates + golden example = **canon doc-output patterns** maintained
  by `/punch-document`.
- Caveman: persisted artifacts use `lite`/`full`, **never Wenyan**; emojis/ASCII
  emoticons allowed in docs (the `/punch-document` carve-out).
- **Provenance.** Lifecycle shapes are **adapted** (not hard-forked) from upstream
  [agent-skills](https://github.com/addyosmani/agent-skills) to fit Punch.

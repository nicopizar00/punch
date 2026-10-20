# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versions follow
Semantic Versioning.

## [Unreleased]

### Added

- Menu workflows: `punch menu` → `Run workflow` picks jobs one step at a
  time, sizes every earlier step for the next one (data sizing, on by
  default), runs one Compose run per step, and writes
  `reports/state/punch-workflow.json`. Planning lives in `punch.workflow`.
- CI lints `src` and `tests` with ruff 0.7.4 before the unit suite: Ruff's
  default rules plus import sorting, and `ruff format --check` at line length
  100 (`ruff.toml`).
- Native k6 config: `spec.k6.config` and `punch run --config <path>` pass the
  load shape as `k6 run --config`; the menu offers presets from `options/`.
- Target data sizing: `spec.sizing` (`iterationSeconds`, `maxSeconds`,
  `margin`), the menu's `Size for a target workflow` mode, and
  `punch run --size-for <target>`.
- Data pickers: before Docker, choose each optional dataset's source and, when
  data is missing, a producer to run instead; `--no-input`,
  `--data <dataset>=default`, `produces[].recommended`, and `dataSources` /
  `switchedFrom` evidence.
- Named datasets in `spec.data` (`produces`, `requires`, `optional`) with
  `--produce` opt-in and atomic publication.
- `punch menu`: interactive workflow picker.

### Changed

- **Breaking:** the YAML unit is a Punch job: `kind: K6Job` (`apiVersion`
  stays `punch/v1`), `punch.job` (`K6Job`, `JobError`, `load_job`),
  `punch.catalog.JobCatalog`, `punch.execution.execute_job`, bundled jobs in
  `jobs/k6/`, the run record key `"job"`, and the `punch-k6-run` parameter
  `job`. A `kind: K6Workflow` file fails with `kind K6Workflow was renamed to
  K6Job; change the kind line`. See ADR 0007.
- After a producer switch the menu asks before writing the switched dataset.

### Removed

- The AI-assisted operating model (prompts, agents, skills, `docs/ai/`),
  `ai.ingest/`, and `punch init`.
- The `ITERATIONS` / `VUS` / `DURATION` load-shape environment contract,
  replaced by the native k6 config.

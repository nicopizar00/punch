# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versions follow
Semantic Versioning.

## [Unreleased]

### Added

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

- After a producer switch the menu asks before writing the switched dataset.

### Removed

- The AI-assisted operating model (prompts, agents, skills, `docs/ai/`),
  `ai.ingest/`, and `punch init`.
- The `ITERATIONS` / `VUS` / `DURATION` load-shape environment contract,
  replaced by the native k6 config.

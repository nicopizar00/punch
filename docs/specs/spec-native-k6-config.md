# Spec — Native k6 config (`k6 run --config`)

> **Status:** Implemented 2026-10-09. Supersedes the environment load-shape
> contract (`ITERATIONS`, `VUS`, `DURATION`) in
> [`spec-target-data-sizing.md`](spec-target-data-sizing.md).

- **Goal** — The load shape of a run is a native k6 options JSON passed with
  `k6 run --config`, not environment names a script turns into `scenarios`.
  Presets are plain k6 config files; any k6 option a config file accepts
  (executor, VUs, iterations, duration, `options.browser`, `maxDuration`)
  works without Punch or the script knowing about it.

- **Decisions (owner, 2026-10-09)**
  - **Config file only.** No load-shape environment overrides remain; sizing
    writes a generated config instead of `ITERATIONS`/`VUS` variables.
  - **YAML default + `--config`.** `spec.k6.config` names a workflow's
    default config; `punch run --config <path>` and the menu's preset picker
    replace it for one run.

- **Contract**
  - `spec.k6.config` (optional): a path beneath `spec.workingDirectory` to a
    JSON object. Load fails when it escapes, is missing, or is not an object.
  - Command: `docker compose run --rm -v <abs config>:/punch/k6-config.json:ro
    … <service> run <script> --config /punch/k6-config.json`. The flag follows
    the script so `k6-wrapper.sh`'s `SCENARIO` rewrite (`$1 $2` = subcommand,
    script) is unchanged. No config → no `-v`, no `--config`.
  - k6 precedence: script `options` outrank `--config`. A script that takes
    its shape from the config must not export `scenarios`, `vus`,
    `iterations`, `duration`, or `stages` (verified on k6 v0.54.0: a script
    `scenarios` block wins over a config file's shortcuts).
  - `punch run --config <path>`: read before Docker; an unreadable file or a
    non-object stops the run. With `--size-for <target>` it names the
    target's shape; without it, the target's `spec.k6.config`; with neither,
    k6 defaults (one iteration).
  - Evidence: each result records `config` (relative to the working
    directory when beneath it).
  - Menu: the preset step appears whenever `options/` (sibling of the
    workflows directory) has presets — no longer gated on forwarded names.
    Entry 0 is `Workflow default (<stem>)` (or `(script options)`); the
    sizing preset cursor starts on the target's own config when it is a
    preset. An unreadable preset warns and falls back to the default.

- **Sizing (`punch.sizing`)**
  - Target shape from one scenario — `shared-iterations` → `iterations`,
    `per-vu-iterations` → `vus × iterations`, `constant-vus` →
    `⌈vus × duration / iterationSeconds⌉` — or k6's top-level shortcut rules
    (`iterations` → shared-iterations; `duration` alone → constant-vus;
    neither → one iteration on one VU, `vus` ignored). Other executors,
    `stages`, more than one scenario, and invalid values are "not estimable"
    reasons, never crashes. k6 defaults (`vus`/`iterations` = 1) apply.
  - A sizable producer needs only `iterationSeconds` + `maxSeconds`.
  - Producer config = the producer's `spec.k6.config` with its execution
    replaced: a single scenario keeps its name and `exec`, `env`, `tags`,
    `options`, `startTime`, `gracefulStop`, `maxDuration`, and becomes
    `shared-iterations` with the sized `vus`/`iterations`; a shortcut config
    drops `vus`/`iterations`/`duration`/`stages` and gets the sized
    shortcuts. Written to `reports/state/k6-config-<producer>.json`.
  - Evidence `sizing.shape` is the normalized target shape
    (`executor`, `vus`, `iterations` | `duration`); `preset` is the target
    config's stem.

- **Non-goals** — Merging several configs, env-var overrides on top of a
  config (k6's own `K6_*` variables still work if a compose service sets
  them), filtering browser-only presets in the menu.

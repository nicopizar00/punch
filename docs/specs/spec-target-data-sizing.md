# Spec — Target data sizing

> **Status:** Draft for human approval (rev 1)

- **Goal** — When the workflow about to run produces a dataset for a target
  workflow, the operator can size the producer run for a target run instead
  of picking producer options directly. The operator picks the target's load
  options; Punch estimates how many rows that target run reads, adds the
  target's error margin, and runs the producer with enough iterations — and
  enough VUs to finish inside its time budget — to write them.

  Example: `http-cart` produces `carts` for `http-orders`. Sizing `http-cart`
  for `http-orders` at `ITERATIONS=50` makes `http-cart` write at least 50
  carts plus margin, so a later `http-orders` run at that shape never wraps
  around its pool.

- **Design principles**
  - Workflow specifics live only in workflow YAML. Pace (seconds per
    iteration), time budget, and margin are declared per workflow in
    `spec.sizing`; target links are the existing `produces[].targets`. No
    workflow or dataset name appears in Python.
  - Punch's load-shape contract is three environment names: `ITERATIONS`,
    `VUS`, `DURATION` — the same convention `BASE_URL` already is. Punch
    reads a target shape only from names the target forwards and writes a
    producer shape only to names the producer forwards.
  - One pure, standard-library-only module (`punch.sizing`) owns the math and
    the eligibility rules; the menu and `punch run` both call it.
  - A workflow is a workflow: one reached through a producer switch gets
    exactly the prompts it gets when picked directly.
  - Still at most one `docker compose run` per invocation. Sizing never
    chains a second run.

- **Non-goals**
  - DURATION support in the parent repo's ITERATIONS-only targets
    (`http-orders`, `http-orders-status`). Their scenarios do not forward
    `DURATION`, so only ITERATIONS shapes size them. Tracked in the parent
    repo as `docs/next-steps/sizing-duration-targets.md`.
  - Ramp modeling. `DURATION` is total wall time; a ramp only lowers real
    throughput, so the estimate errs toward more rows.
  - Learning pace from previous summaries. `iterationSeconds` is declared,
    not measured by Punch.
  - Rows per iteration other than one, on either side. Every current
    consumer reads one row per iteration and every producer writes one.
  - Configurable load-shape environment names.
  - Sizing for the `all` selector.
  - Chained sizing (sizing a producer's own inputs). A producer that reads a
    dataset smaller than its sized iteration count gets a warning only.
  - The parent repository's deprecated `./dev perf:*` glue.

- **YAML schema change** — `spec` gains one optional block:

  ```yaml
  spec:
    sizing:
      iterationSeconds: 1.0   # optional, number > 0: wall time of one iteration on one VU
      maxSeconds: 270         # optional, number > 0: run-time budget when this workflow is a sized producer
      margin: 0.15            # optional, number >= 0, default 0: extra fraction of rows when this workflow is a sizing target
  ```

  Unknown keys, non-numeric values, booleans, and out-of-range values are a
  `WorkflowError`. An empty `sizing` mapping is valid and makes the workflow
  a target with zero margin and ITERATIONS-only sizing.

- **Definitions**
  - *Sizable producer* — a workflow whose `spec.sizing` sets both
    `iterationSeconds` and `maxSeconds`, and whose
    `spec.environment.forward` contains `ITERATIONS` and `VUS`.
  - *Sizable target* — a workflow with a `spec.sizing` block.
  - *Sizing pair* — `(dataset, target)` for each `produces[].targets` entry
    of a sizable producer whose target is sizable. Pairs are ordered by
    product order, then target order, as declared.
  - *Target shape* — the subset of a load source (a menu options preset, or
    the process environment for `punch run`) restricted to `ITERATIONS`,
    `VUS`, `DURATION` and to the names the target forwards.
  - *Rows needed* `Y`:
    - shape has `ITERATIONS` (positive integer) → `Y = ITERATIONS`, whatever
      else it has (every scenario lets `ITERATIONS` win);
    - else shape has `DURATION` (k6 duration string: one or more
      `<number><unit>` groups, units `ms`, `s`, `m`, `h`) and `VUS`
      (positive integer), and the target declares `iterationSeconds` →
      `Y = ceil(VUS × seconds(DURATION) / iterationSeconds)`;
    - else the shape is *not estimable*, with the first matching reason:
      `<T> forwards neither ITERATIONS nor DURATION`, `shape sets neither
      ITERATIONS nor DURATION`, `DURATION needs VUS`, `<T> declares no
      sizing.iterationSeconds`, `invalid <NAME> "<value>"`.
  - *Producer iterations* `N = ceil(Y × (1 + margin))`, `margin` from the
    target.
  - *Producer VUS* `V = min(N, max(1, ceil(N × iterationSeconds_p /
    maxSeconds_p)))` — k6 rejects more VUs than shared iterations.
  - *Estimated producer seconds* `N × iterationSeconds_p / V`.
  - *Sized datasets* — for the chosen target, every dataset the producer
    produces whose `targets` include it.
  - Every ceiling above is taken on exact decimal values of the YAML and
    shape numbers (e.g. `fractions.Fraction(str(x))`), never on binary
    floats, so `ceil(20 × 1.1) = 22`.

- **Functional requirements**
  1. `workflow.Sizing(iteration_seconds: float | None, max_seconds: float |
     None, margin: float = 0.0)`; `K6Workflow.sizing: Sizing | None = None`;
     `SPEC_KEYS` gains `sizing`; strict parse as above.
  2. `punch.sizing` (new, standard-library only):
     - `SizingError(ValueError)` carries the not-estimable or ineligible
       reason.
     - `sizing_pairs(producer, catalog) -> tuple[tuple[str, str], ...]`.
     - `target_shape(target, source: Mapping[str, str]) -> dict[str, str]`.
     - `rows_needed(target, shape) -> int` (raises `SizingError`).
     - `SizingPlan(producer, target, datasets, shape, rows_needed, margin,
       iterations, vus, estimated_seconds, preset: str | None = None)`.
     - `size_producer(producer, target, catalog, source, *, preset=None) ->
       SizingPlan` — raises `SizingError` when the producer is not sizable
       (`<P> is not a sizable producer: needs sizing.iterationSeconds,
       sizing.maxSeconds, and forwarded ITERATIONS and VUS`), the target is
       not one of its sizing pairs (`<P> does not produce data for <T>`), or
       the shape is not estimable.
     - `producer_environment(environment, plan) -> dict[str, str]` — a copy
       with `ITERATIONS=N`, `VUS=V`, and `DURATION` removed.
     - `summary_lines(plan) -> list[str]`:
       ```
       [punch] sizing <P> for <T> (<preset> | <NAME>=<value> …)
         rows needed   : <Y>  (<NAME>=<value> …)
         margin <m>%   : <N> producer iterations
         producer VUS  : <V>  (~<s>s of <maxSeconds>s budget)
       ```
       plus `[punch] warning: one <P> iteration exceeds its <maxSeconds>s
       budget` when `estimated_seconds > maxSeconds`.
     - `input_warnings(plan, row_counts: Mapping[str, int]) -> list[str]` —
       for each dataset the producer reads with fewer rows than `N`:
       `[punch] warning: <P> reads "<D>" (<r> rows) but runs <N>
       iterations; rows repeat`.
     - `shortfall(plan, produced: Mapping[str, int]) -> list[str]` — for
       each sized dataset with fewer rows than `Y`: `[punch] warning: "<D>"
       has <r> rows; <T> needs <Y>`.
     - `next_hint(plan, produced) -> str` — `[punch] <D> ready (<r> rows);
       run <T> next with <preset | NAME=value …>.`
  3. **`punch` menu**, after `plan_data` and the base URL picker, for the
     workflow `W` that will run (picked or switched to — no difference):
     1. No sizing pair → today's options and produce prompts.
     2. Otherwise an options-mode picker: `Options as usual` (cursor) /
        `Size for a target workflow`. Esc cancels the menu as on every
        screen.
     3. *Usual* → today's options preset picker and produce prompts.
     4. *Size* → a pair picker `"<D>" for <T>` (skipped with
        `[punch] sizing for <T> ("<D>")` when there is one pair), then the
        options preset picker titled `Load options for <T>:` listing every
        preset; a preset whose target shape is not estimable shows
        `  (not estimable: <reason>)` and re-asks when picked. No `Skip`
        entry. Then `summary_lines`, then `input_warnings`.
     5. Sized datasets are written without asking (`[punch] writing "<D>"
        (sized for <T>)`); other datasets of `W` are asked as today.
     6. The Docker environment is `producer_environment` over the base
        environment plus base URL; no producer preset is applied.
     7. After a passing sized run: `shortfall` warnings, then `next_hint`.
        Exit code unchanged by a shortfall.
     8. **Supersedes the data-pickers spec:** after a producer switch, the
        menu no longer writes the switched dataset unasked. `W` gets the
        same produce prompts as a direct pick (sized datasets excepted, as
        above). `switch_hint` after a passing switched run is unchanged.
  4. **`punch run <name> --size-for <T>`**:
     - Single-workflow selector only; with `all` → error before Docker
       (same message pattern as `--produce`/`--data`).
     - `size_producer(selected, T, catalog, os.environ)`; `SizingError` →
       reason on stderr, evidence result fails with it, exit 1, no Docker.
     - Sized datasets join `--produce` (deduplicated).
     - `input_warnings` and `summary_lines` print before the Docker call;
       the Docker environment is `producer_environment(os.environ, plan)`.
     - After a passing run: `shortfall` warnings and `next_hint`.
     - Evidence result gains `"sizing"`: `target`, `datasets`, `shape`,
       `preset` (null on the CLI), `rowsNeeded`, `margin`,
       `producerIterations`, `producerVus`, `producedRows` (dataset → row
       count), `short` (bool).
     - A TTY data-picker switch drops `--size-for` like `--produce`/`--data`:
       `[punch] ignoring --produce/--data/--size-for for <W>` naming only
       the flags given. The switched producer keeps its implied
       `--produce` (the CLI has no prompt to ask).
  5. `punch.data_plan` and `punch.execution` are unchanged; row counts for
     `input_warnings` come from the existing `used_data_paths` and
     `data_row_count`.
  6. **Parent repository:**
     - Every workflow in `tests/performance/k6/workflows/` that produces for
       a target, and every target, declares `spec.sizing`.
       `iterationSeconds` is measured: `VUS=1 ITERATIONS=10` run,
       `durationMs / 10 / 1000`, rounded up to 0.1 s. `maxSeconds: 270` on
       every producer (30 s under the scenarios' 5 m `maxDuration`).
       `margin: 0.15` on every target unless a measured error rate says
       otherwise.
     - `tests/performance/k6/README.md` documents sizing (menu and
       `--size-for`).
     - `docs/next-steps/sizing-duration-targets.md` records the DURATION
       follow-up.

- **Acceptance criteria**
  - A workflow with no sizing pair: menu and `punch run` behave as today,
    no options-mode picker.
  - Menu, sizing `http-cart` for `http-orders` with `5-iterations`: summary
    `rows needed 5`, `6 producer iterations` (margin 0.15), Docker command
    has `-e ITERATIONS=6 -e VUS=<V>` and no `DURATION`; `carts` written
    without asking; one Compose run.
  - DURATION shape on a DURATION-forwarding target: `Y = ceil(VUS × seconds
    / iterationSeconds)`; `1h30m` parses to 5400 s.
  - DURATION preset on an ITERATIONS-only target: shown as not estimable;
    picking it re-asks.
  - `ITERATIONS` beats `DURATION` in one shape.
  - `V` never exceeds `N`; `V ≥ 1`; budget warning when one iteration
    exceeds `maxSeconds`.
  - `Y = 20`, `margin: 0.1` → `N = 22` (exact decimal ceiling).
  - Producer reading a dataset smaller than `N`: input warning, run
    continues.
  - Produced rows below `Y`: shortfall warning and `"short": true`; exit
    code unchanged.
  - Picked directly or switched to, a producer sees the same prompts.
  - `--size-for` with `all`, an unknown target, a non-target, a
    non-sizable producer, or a non-estimable env shape: exit 1, zero Docker
    calls, reason in evidence.
  - Switch under `--size-for`: flag ignored with a message, producer runs
    with its implied `--produce`.
  - Invalid `spec.sizing` (unknown key, `margin: -1`, `iterationSeconds:
    0`, `maxSeconds: true`) → `WorkflowError`.
  - No workflow or dataset literal added to `src/punch/*.py`.

- **Testing** — `unittest`: new `test_sizing.py` (shapes, forward
  filtering, precedence, duration parsing, margin and VUS rounding, clamp,
  eligibility, warnings, hints); `test_workflow.py` (schema);
  `test_menu.py` (mode picker, pair picker, preset re-ask, forced sized
  write, switch parity); `test_cli.py` (`--size-for` errors, environment
  override, implied produce, evidence, switch drop). Docker stubbed; tests
  assert Compose-run counts and commands. One live sized run in the parent
  repo (`http-cart` for `http-orders`, `ITERATIONS=5`) passes with no
  shortfall.

- **Docs** — `README.md` and `docs/workflows/validation.md` (`spec.sizing`,
  options mode, `--size-for`, `sizing` evidence, switch prompt change);
  `CLAUDE.md` data line; `CHANGELOG.md`.

- **Risks**
  - Declared pace drifts from reality (heavier target, more VUs, slower
    environment). Mitigated: target pace measured at 1 VU overstates rows
    under contention (safe side); producer budget keeps 30 s headroom; the
    shortfall check reports a miss.
  - Producer pace slows under the computed VUs and the run hits the
    scenario's `maxDuration`. Mitigated: shortfall warning; raise
    `iterationSeconds` or lower `maxSeconds` in YAML.
  - Dropping the forced write after a switch adds one prompt to switched
    menu runs; answering `n` runs the producer without writing. Accepted:
    the operator asked for direct-pick parity.

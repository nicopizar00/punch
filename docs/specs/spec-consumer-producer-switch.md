# Spec — Data source selection and producer switch

> **Status:** Draft for human approval (rev 4 — design review fixes:
> `--no-input`, `--data <dataset>=default`, `PlanStop` reasons, forced
> produce after a switch, menu Esc semantics, `dataSources` evidence,
> still-missing hint, missing-environment tag, `data_plan` module)

- **Goal** — Before any Docker prompt or Docker call, Punch settles where each
  dataset a workflow reads comes from. Interactively:
  - every optional dataset with more than one available source offers them
    (built-in default or `data/<dataset>.csv`) in an arrow-key list;
  - then the normal data preflight runs; when a dataset the run reads has no
    rows, an arrow-key list of every producer of it is offered, the
    recommended one labeled and preselected. Picking one continues the
    normal flow with that producer in place of the selected workflow.

  Every interactive choice has a non-interactive CLI equivalent. Every
  invocation still launches at most one `docker compose run`.

- **Design principles**
  - Workflow specifics live only in workflow YAML (`spec.data.produces`,
    `recommended`, `requires`, `optional`, `spec.environment.required`).
    Punch derives sources, producers, recommendations, and tags from the
    catalog; no workflow or dataset name appears in Python.
  - One shared, parameterized implementation serves every entry point:
    - `punch.data_plan` (new, standard-library-only module) owns the picker
      contract, the walk, labels, and stop reasons.
    - `punch.execution` owns only data primitives used to run (paths, row
      counts, choices, environment, evidence sources).
    - `menu.choose` is the only terminal picker; `punch run` and the
      `punch` menu both use it.
  - `execute_workflow` stays non-interactive; it accepts the chosen optional
    sources and keeps its preflight as a backstop with an unchanged message.

- **Non-goals**
  - More than one `docker compose run` per invocation. A replaced workflow is
    never run afterward; the user re-runs it.
  - Resolving more than one missing dataset per invocation (one Compose run
    produces one dataset; the hint names what is still missing).
  - Row-count thresholds. "Has data" keeps meaning ≥1 row after the header.
  - Alternative-dataset groups (one input satisfied by one of several
    datasets). Each optional dataset is its own picker.
  - Any behavior based on `targets`. The key stays (required, validated as
    today) for a future "pre-calculate data" feature.
  - `punch doctor` changes.
  - The parent repository's deprecated `./dev perf:*` glue
    (`scripts/pg/k6runner.py`) — untouched, must keep importing and working.
  - Pickers for the `all` selector.

- **YAML schema change** — `spec.data.produces[]` gains one optional key:

  ```yaml
  produces:
    - dataset: carts
      columns: [cartId, productId, sid]
      targets: [http-orders]
      recommended: true        # optional, boolean, default false
  ```

  `recommended: true` marks the preferred producer of that dataset for every
  consumer. Non-boolean values are a `WorkflowError`. When several producers
  of a dataset are marked, the first by workflow name wins; no error.

- **CLI change (`punch run`)**
  - `--no-input` — never open a picker; today's automatic behavior.
  - `--data <dataset>=default` — use the built-in default for an optional
    dataset (no `DATA_<D>_CSV`), even when its file has rows. A required
    dataset with `=default` is an error before Docker.
  - `--data <dataset>=<path>` — unchanged.

- **Definitions**
  - *Optional choices* — `Mapping[str, bool]`, dataset → `True` (read its
    path) / `False` (default). An absent key means the automatic rule: read
    the path only when it has rows.
  - *Datasets read by a run* — required datasets plus optional ones whose
    choice (or automatic rule) selects the path.
  - *Missing dataset* — a dataset read by the run whose path is absent or has
    no rows.
  - *Available sources* of optional dataset `D` — always `default
    (built-in)`; plus its path when it has rows, or when it has no rows but
    some workflow produces `D` (picking it leads to the producer picker).
  - *Producers* of `D` — every catalog workflow producing `D`, sorted by
    name. *Recommended producer* — the first of them whose product sets
    `recommended: true`, else none.
  - *Data sources* (evidence) — for each required and optional dataset of
    the executed workflow: its path relative to the working directory when
    read, else `"default"`.

- **Functional requirements**
  1. `workflow.DataProduct.recommended: bool = False`.
  2. `catalog.WorkflowCatalog.recommended_producer(dataset) -> str | None`.
  3. `execution` primitives (all `optional_choices: Mapping[str, bool] |
     None = None`, `None` = automatic rule):
     - `data_row_count(path) -> int`;
     - `used_data_paths`, `absent_optional_datasets`, `data_environment`,
       `preflight_requirements`, `execute_workflow` accept
       `optional_choices`;
     - `missing_datasets(workflow, overrides, optional_choices=None)`;
       `preflight_requirements` is built on it, message unchanged;
     - `resolve_data_args(workflow, raw) -> tuple[dict[str, Path],
       dict[str, bool]]` splits `--data` into path overrides (via the
       unchanged `resolve_data_overrides`) and `=default` choices;
     - `data_sources(workflow, overrides, optional_choices=None) ->
       dict[str, str]`;
     - the absent-optional note becomes
       `[punch] optional dataset "<D>" not used — scenario uses its default`.
  4. `punch.data_plan`:
     - `Choice(title, options, cursor=0)`; `Chooser = Callable[[Choice],
       int | None]`; `DataPlan(workflow, overrides, optional_choices,
       produce=(), switched_from=())`; `PlanStop(reason: str, canceled:
       bool = False)`; `CANCELED = "data selection canceled"`.
     - `plan_data(workflow, catalog, overrides, optional_choices, *, choose,
       environment) -> DataPlan | PlanStop`. Per current workflow:
       1. For each optional dataset without a preset choice and without a
          path override: when only `default` is available, choose it
          silently; otherwise call `choose` — title `"<W>" can read "<D>" —
          pick a source (Esc cancels)`, options `default (built-in)` and
          `<rel> (<N> rows)` / `<rel> (no rows)`, cursor 0. Esc →
          `PlanStop(CANCELED, canceled=True)`.
       2. No missing dataset → return the `DataPlan`.
       3. First missing dataset `D` at `<rel>`: no producer →
          `PlanStop('"<W>" needs "<D>" (no rows at <rel>) and no workflow
          produces it')`. Otherwise call `choose` — title `"<W>" needs
          "<D>" (no rows at <rel>). Run a producer instead? (Esc cancels)`,
          one option per producer: `<name>` plus `  (<tags>)` where tags are
          `recommended` and/or `needs <ENV>, …` (its
          `spec.environment.required` names missing from `environment`);
          cursor on the recommended one, else 0. Esc →
          `PlanStop('data selection canceled; "<W>" needs "<D>" (no rows at
          <rel>) — produce it with: <producers> (--produce <D>)',
          canceled=True)`.
       4. A producer already visited → `PlanStop("producer cycle: a → b →
          a")`. Otherwise it becomes the current workflow (no overrides, no
          preset choices, `produce=(D,)`) and the step repeats.
     - `switch_hint(plan, catalog) -> str` →
       `[punch] <D> ready; run <origin> next.` or
       `[punch] <D> ready; run <origin> next (still missing: <X>, …).` where
       `<origin>` is `switched_from[0]` and still-missing are its missing
       required datasets after the run.
  5. `menu.choose(choice) -> int | None` renders a `Choice` with the existing
     `_select`/`TerminalMenu` helper; Esc → `None`. A non-TTY stdin or an
     unavailable terminal menu is distinct from Esc: `choose` raises
     `PickerUnavailable` (defined in `data_plan`), which `plan_data` does not
     catch.
  6. **`punch` menu**: `plan_data` (with `os.environ`) runs right after
     workflow selection, before base URL, options, produce, and Docker
     prompts.
     - `PickerUnavailable` → treated as an unavailable menu: `run_menu`
       reports `[punch] interactive menu requires a terminal.`, exit 1.
     - `PlanStop` with `canceled` → `[punch] menu canceled.`, exit 0 (same
       as Esc on any other menu screen). Other `PlanStop` → reason on
       stderr, exit 1. No Docker prompt either way.
     - With a plan, every later step uses `plan.workflow` and
       `plan.optional_choices`. Datasets in `plan.produce` are written
       without asking (`[punch] writing "<D>" (needed by the selected
       workflow)`); other produced datasets are asked as today.
     - After a passing switched run, print `switch_hint`.
  7. **`punch run <name>`**, single-workflow selector, TTY stdin, no
     `--no-input`: `plan_data` (with `menu.choose`, the `--data` overrides
     and `=default` choices as presets, `os.environ`) runs before the
     collision check and any Docker call.
     - `PickerUnavailable` (TTY stdin but no usable terminal menu) → print
       `[punch] no interactive terminal for pickers; using automatic data
       sources` and continue as the non-interactive path: no plan, no stop,
       no evidence failure.
     - `PlanStop` → reason on stderr; evidence result for the selected
       workflow fails with the reason; exit 1; no Docker.
     - Switched plan → the producer runs with exactly `plan.produce`; the
       selected workflow's `--produce`/`--data` are dropped with
       `[punch] ignoring --produce/--data for <W>` when present; collision
       checks, log path, and evidence use the producer; evidence carries
       `"switchedFrom"`; `tests` lists the workflow that ran; after a pass,
       print `switch_hint`.
     - Every run (interactive or not) passes its optional choices to
       `execute_workflow` and the consumed-data delete prompt, and records
       `"dataSources"` in evidence when the workflow reads any dataset.
     - Non-TTY, `--no-input`, or `all`: no pickers; `=default` choices still
       apply.
  8. Parent repository YAML marks `recommended: true` on `http-cart`'s
     `carts` and `http-purchase`'s `orders`.

- **Acceptance criteria**
  - Present required data, no optional data: no picker, unchanged behavior.
  - Optional dataset with rows (or no rows but a producer), TTY: source
    picker, cursor on default; default → no `DATA_<D>_CSV`; file with rows →
    injected; file without rows → producer picker.
  - Optional dataset with no rows and no producer: no picker, default.
  - Missing required data, TTY: producer picker lists every producer,
    recommended labeled/preselected, missing-environment tag shown; pick runs
    exactly one `docker compose run` with `--produce <D>`; selected workflow
    never launched.
  - Two-level chain shows two producer pickers, runs only the root producer;
    hint names the origin and what it still misses.
  - `punch run` Esc → exit 1, zero Docker calls, reason in evidence. Menu Esc
    → `menu canceled`, exit 0, zero Docker calls.
  - `--no-input` in a TTY → no picker. `--data D=default` → default without
    picker; on a required dataset → error before Docker.
  - Menu after a switch never asks to write the switched dataset.
  - Cycle → `PlanStop`, no Docker call.
  - Non-boolean `recommended` → `WorkflowError`; two recommended → first by
    name.
  - Evidence: `switchedFrom` only on switched runs; `dataSources` on every
    run of a data-reading workflow.
  - No workflow or dataset literal added to `src/punch/*.py`; parent
    `scripts/pg/k6runner.py` still imports and its tests pass.

- **Testing** — `unittest`: `test_workflow.py`, `test_catalog.py`,
  `test_execution.py` (choices, missing, row count, `resolve_data_args`,
  `data_sources`), new `test_data_plan.py` (scripted chooser: sources,
  producers, tags, chain, cycle, stops, presets, hint), `test_cli.py` and
  `test_menu.py` (real `menu.choose`, `TerminalMenu` stubbed). Docker is
  stubbed; tests assert compose-run counts. Parent: `pnpm pg:test`.

- **Docs** — `AGENTS.md` prompts line; `README.md` and
  `docs/workflows/validation.md` (`recommended`, pickers, `--no-input`,
  `--data D=default`, `switchedFrom`, `dataSources`); parent
  `tests/performance/k6/README.md`.

- **Risks**
  - The source picker adds a prompt per optional dataset in interactive
    runs. Mitigated: Enter keeps default; `--no-input` / `--data D=…` skip it.
  - A switched run writes a dataset not named on the command line.
    Mitigated: only after an explicit pick.
  - The missing-environment tag reads the environment at plan time; a menu
    options preset chosen later may supply it. Tag is advisory only.

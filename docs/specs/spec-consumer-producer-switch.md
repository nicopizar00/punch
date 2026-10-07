# Spec — Data source selection and producer switch

> **Status:** Draft for human approval (rev 3 — dataset-wide `recommended`,
> optional-source picker, one generic picker, `targets` out of scope)

- **Goal** — Before any Docker prompt or Docker call, Punch settles where each
  dataset a workflow reads comes from. In an interactive terminal:
  - every optional dataset offers its sources (built-in default or
    `data/<dataset>.csv`) in an arrow-key list;
  - then the normal data preflight runs; when a dataset the run reads has no
    rows, an arrow-key list of every producer of it is offered, the
    recommended one labeled and preselected. Picking one continues the
    normal flow with that producer in place of the selected workflow.

  Every invocation still launches at most one `docker compose run`.

- **Design principles**
  - Workflow specifics live only in workflow YAML (`spec.data.produces`,
    `recommended`, `requires`, `optional`). Punch derives sources,
    producers, and recommendations from the catalog; no workflow or dataset
    name appears in Python.
  - One shared, parameterized implementation serves every entry point:
    - `execution.plan_data` — one per-workflow step (optional-source pickers
      → preflight → producer picker), repeated for a picked producer;
      standard-library-only; builds every picker label itself.
    - One generic picker contract `Choice(title, options, cursor) → index |
      None`; `menu.choose` is its only terminal implementation, used by
      `punch run` and the `punch` menu.
  - `execute_workflow` stays non-interactive; it accepts the chosen optional
    sources and keeps its preflight as a backstop with an unchanged message.

- **Non-goals**
  - Running more than one `docker compose run` per invocation. A replaced
    workflow is never run afterward; the user re-runs it.
  - Row-count thresholds. "Has data" keeps meaning ≥1 row after the header.
  - Alternative-dataset groups (one input satisfied by one of several
    datasets). Each optional dataset is its own picker.
  - Any behavior change based on `targets`. The key stays in the schema
    (required, validated as today) for a future "pre-calculate data"
    feature; this design ignores it.
  - `punch doctor` changes.
  - The parent repository's deprecated `./dev perf:*` glue
    (`scripts/pg/k6runner.py`).
  - Switching or pickers for the `all` selector.

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

- **Definitions**
  - *Sources* of a dataset `D` read by workflow `W`:
    - `D` in `requires` → one source: its data path (`data/D.csv`, or the
      `--data D=<path>` override).
    - `D` in `optional` → two sources: `default (built-in)` (no
      `DATA_D_CSV` injected; the scenario uses its own data) and its data
      path.
  - *Optional choices* — `Mapping[str, bool]`, dataset → `True` (read the
    data path) / `False` (default). An absent key means today's automatic
    rule: read the data path only when it has rows.
  - *Datasets read by a run* — required datasets plus optional datasets whose
    choice (or automatic rule) selects the data path.
  - *Missing dataset* — a dataset read by the run whose path is absent or has
    no rows. With no choices this equals today's preflight rule.
  - *Producers* of `D` — every catalog workflow that produces `D`, sorted by
    name. *Recommended producer* — the first of them whose `D` product has
    `recommended: true`, else none.

- **Functional requirements**
  1. `workflow.DataProduct.recommended: bool = False`, parsed from the
     optional key above.
  2. `catalog.WorkflowCatalog.recommended_producer(dataset) -> str | None`
     implements the definition.
  3. `execution` gains optional-choice awareness, all defaulting to today's
     behavior when `optional_choices` is `None`:
     `used_data_paths`, `absent_optional_datasets`, `data_environment`,
     `preflight_requirements`, and `execute_workflow` accept
     `optional_choices: Mapping[str, bool] | None = None`.
     `missing_datasets(workflow, overrides, optional_choices=None) ->
     tuple[str, ...]` is new; `preflight_requirements` is built on it with
     its message unchanged. The absent-optional note becomes
     `[punch] optional dataset "<D>" not used — scenario uses its default`.
  4. Generic picker contract in `execution`:
     `Choice(title: str, options: tuple[str, ...], cursor: int = 0)` (frozen)
     and `Chooser = Callable[[Choice], int | None]`.
  5. `execution.plan_data(workflow, catalog, overrides, *, choose, stdout) ->
     DataPlan | None`, where `DataPlan` is frozen `(workflow, overrides,
     optional_choices, produce, switched_from)`. For the current workflow:
     1. For each optional dataset in declaration order: if it has an
        override, choose the data path without asking; otherwise call
        `choose` with title `"<W>" can read "<D>" — pick a source (Esc
        cancels)`, options `default (built-in)` and `<relative path>
        (<N> rows)` / `<relative path> (no rows)`, cursor `0` (default).
     2. Compute missing datasets with those choices. None → return the plan.
     3. Otherwise, for the first missing dataset call `choose` with title
        `"<W>" needs "<D>" (no rows at <relative path>). Run a producer
        instead? (Esc cancels)`, one option per producer, the recommended
        one suffixed `  (recommended)`, cursor on it else `0`.
     4. The picked producer becomes the current workflow (its overrides
        empty, `produce=(D,)`) and the step repeats from 1.
     - Esc (`None`) at any picker returns `None`.
     - A producer already visited ends the walk with `None` and a
       `[punch] producer cycle: a → b → a` line on `stdout`.
     - `switched_from` lists the workflows left behind, in order.
  6. `menu.choose(choice: Choice) -> int | None` renders the contract with the
     existing `_select`/`TerminalMenu` helper (`cursor_index=choice.cursor`);
     Esc, non-TTY stdin, or an unavailable terminal menu return `None`.
  7. `switch_hint(plan) -> str` →
     `[punch] <produce[0]> ready; run <switched_from[0]> next.` Printed by
     both entry points after a passing run whose plan switched workflows.
  8. **`punch` menu** (`menu._run_workflow_menu`): `plan_data` runs right
     after workflow selection, before base URL, options, produce, and Docker
     prompts. With a plan, every later step uses `plan.workflow` and
     `plan.optional_choices`; `_choose_produce` defaults `plan.produce`
     datasets to "Y". With `None`, the menu prints the preflight message for
     the selected workflow (or `data selection canceled`) to stderr and
     returns 1 before any Docker prompt.
  9. **`punch run <name>`** (`__main__.cmd_run`), single-workflow selector
     with a TTY stdin only: `plan_data` (with `menu.choose`) runs before the
     collision check and any Docker call.
     - With a plan that switched: the producer runs with exactly
       `plan.produce`; the selected workflow's `--produce`/`--data` are
       dropped with one `[punch] ignoring --produce/--data for <W>` line when
       present; collision checks, log path, and evidence use the producer;
       its evidence result carries `"switchedFrom": [...]`; `tests` lists the
       workflow that ran.
     - Every plan's `optional_choices` reach `execute_workflow` and the
       consumed-data delete prompt.
     - With `None`: no Docker call; the evidence result for the selected
       workflow fails with the preflight message (or `data selection
       canceled`); exit 1.
     - Non-TTY stdin or `all`: no pickers, today's behavior.
  10. Parent repository workflow YAML marks `recommended: true` on
      `http-cart`'s `carts` and `http-purchase`'s `orders` (HTTP producers
      are faster than browser ones; `http-purchase` needs no upstream data).

- **Acceptance criteria**
  - Workflow with only present required data: no picker, unchanged behavior.
  - Workflow with an optional dataset, TTY: source picker always shown,
    cursor on `default (built-in)`; default → no `DATA_<D>_CSV`; data path
    with rows → injected; data path without rows → producer picker.
  - Required dataset missing, TTY: producer picker lists every producer,
    recommended labeled and preselected; arrow keys can pick another; the
    pick runs exactly one `docker compose run` with `--produce <D>`; the
    selected workflow is never launched.
  - Two-level chain (`orders-status` ← `orders` ← `carts`) shows two producer
    pickers and runs only the root producer.
  - Esc → exit 1, zero Docker calls, preflight message or
    `data selection canceled`.
  - Non-TTY `punch run`: no picker, today's behavior and messages.
  - Cycle → `None`, cycle line, no Docker call.
  - Non-boolean `recommended` → `WorkflowError`; two recommended producers →
    first by name.
  - `punch-run.json` has `switchedFrom` only on switched runs.
  - No workflow or dataset literal added to `src/punch/*.py`.

- **Testing** — `unittest` in `tests/`: `test_workflow.py` (`recommended`
  parse/default/type), `test_catalog.py` (recommended lookup, first wins),
  `test_execution.py` (optional choices through `used_data_paths` /
  `missing_datasets` / `execute_workflow`; `plan_data` with a scripted
  chooser: source picker, producer picker, chain, cycle, cancel, overrides),
  `test_cli.py` and `test_menu.py` (real `menu.choose` with `TerminalMenu`
  stubbed: options, labels, `cursor_index`, switch run, optional source,
  cancel, evidence, non-TTY, `all`). Docker is stubbed; tests assert the
  compose-run count.

- **Docs** — `AGENTS.md` key-patterns line on interactive prompts names the
  source and producer pickers; `README.md` and `docs/workflows/validation.md`
  describe `recommended`, both pickers, and `switchedFrom`; the parent
  repository's `tests/performance/k6/README.md` mentions both.

- **Risks**
  - The source picker adds one prompt per optional dataset to every
    interactive run of such a workflow. Accepted: the user asked for it to
    always be offered; Enter keeps the default.
  - A switched run writes a dataset the user did not name on the command
    line. Mitigated: only after an explicit pick, only in a TTY.

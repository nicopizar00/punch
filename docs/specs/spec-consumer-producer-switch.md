# Spec — Consumer data preflight with producer switch

> **Status:** Draft for human approval (rev 2 — producer-side `recommended`,
> arrow-key producer picker)

- **Goal** — When a consumer workflow is selected and a required dataset has
  no data rows, Punch says so *before* any Docker prompt or Docker call and,
  in an interactive terminal, lets the operator pick a producer workflow from
  an arrow-key list of every compatible producer, with the recommended one
  labeled and preselected. Picking one continues the normal flow with that
  producer in place of the consumer. Every invocation still launches at most
  one `docker compose run`.

- **Design principles**
  - Workflow specifics live only in workflow YAML (`spec.data.produces`,
    `targets`, `recommended`, `requires`). Punch derives producers,
    recommendations, and chains from the catalog; no workflow or dataset name
    appears in Python.
  - One shared, parameterized implementation serves every entry point. The
    switch walk (`execution.offer_producer_switch`) is standard-library-only
    and takes the picker as a parameter; the one terminal picker
    (`menu.choose_producer`) is shared by `punch run` and the `punch` menu.
  - `execute_workflow` stays non-interactive. Its preflight remains as a
    backstop for direct API callers, with its message unchanged.

- **Non-goals**
  - Running more than one `docker compose run` per invocation. The consumer
    is never run after the producer; the user re-runs it.
  - Row-count thresholds (`minRows`, comparing rows to `ITERATIONS`). "Enough
    data" keeps its current meaning: at least one data row.
  - Optional datasets (`spec.data.optional`). They never trigger a switch.
  - The parent repository's `./dev perf:*` glue (`scripts/pg/k6runner.py`) —
    deprecated, untouched.
  - Per-consumer recommendation scopes (`recommended` covers all of a
    product's `targets`).
  - Switching for the `all` selector.

- **YAML schema change** — `spec.data.produces[]` gains one optional key:

  ```yaml
  produces:
    - dataset: carts
      columns: [cartId, productId, sid]
      targets: [http-orders]
      recommended: true        # optional, boolean, default false
  ```

  `recommended: true` marks this producer as the preferred way to produce the
  dataset for every workflow in its `targets`. Non-boolean values are a
  `WorkflowError`. The catalog rejects two producers of the same dataset that
  are both `recommended` and share a target.

- **Definitions**
  - *Missing dataset* — a dataset in `spec.data.requires` whose resolved path
    (default or `--data` override) is absent or has no rows after the header.
    Same rule `preflight_requirements` uses today.
  - *Compatible producers* of dataset `D` — every catalog workflow that
    produces `D` (`producers_of(D)`, sorted by name). The catalog already
    guarantees they declare identical columns.
  - *Recommended producer* of `D` for consumer `C` — the compatible producer
    whose `D` product has `recommended: true` and lists `C` in `targets`;
    `None` when there is none.

- **Functional requirements**
  1. `workflow.DataProduct` gains `recommended: bool = False`, parsed from the
     optional key above.
  2. `catalog.WorkflowCatalog.recommended_producer(dataset, consumer) ->
     str | None` implements the definition. Catalog loading raises
     `CatalogError('"<dataset>" has more than one recommended producer for
     <target>: <a>, <b>')` on a conflict.
  3. `execution.missing_datasets(workflow, overrides) -> tuple[str, ...]`
     returns missing required datasets in declaration order. Pure.
     `preflight_requirements` is reimplemented on top of it; its message text
     is unchanged.
  4. `execution.offer_producer_switch(workflow, catalog, overrides, *,
     choose, stdout) -> ProducerSwitch | None`, where `ProducerSwitch` is a
     frozen dataclass `(workflow: K6Workflow, produce: tuple[str, ...],
     switched_from: tuple[str, ...])` and `choose` is a
     `ProducerChooser = Callable[[ProducerChoice], str | None]` receiving a
     frozen `ProducerChoice(consumer: str, dataset: str, path: Path,
     producers: tuple[str, ...], recommended: str | None)`:
     - Returns `None` immediately when nothing is missing (no `choose` call).
     - Calls `choose` for the first missing dataset; `None` from `choose`
       returns `None`.
     - A picked producer becomes the current workflow with
       `produce=(dataset,)`, and the check repeats on it. A producer that is
       itself a consumer with missing data (e.g. `orders` ← `carts`) gets its
       own `choose` call. The walk stops at the first workflow whose required
       data is present.
     - A workflow already visited in the walk ends it with `None` and a
       `[punch] producer cycle: a → b → a` line on `stdout`.
     - `switched_from` lists the workflows left behind, in order.
     - The producer's data check ignores the consumer's `--data` overrides.
  5. `menu.choose_producer(choice: ProducerChoice) -> str | None` is the one
     terminal picker:
     - Uses the same `TerminalMenu` helper as workflow selection.
     - Title: `"<consumer>" requires "<dataset>" (no rows at <path>). Run a
       producer instead? (Esc cancels)`.
     - One row per compatible producer; the recommended row reads
       `<name>  (recommended)`; the cursor starts on the recommended row, else
       on the first.
     - Enter returns that producer. Esc, a non-TTY stdin, or an unavailable
       terminal menu returns `None`.
  6. **`punch` menu** (`menu._run_workflow_menu`): `offer_producer_switch`
     runs right after workflow selection, before base URL, options, produce,
     and Docker prompts. On a switch, every later step uses the producer, and
     `_choose_produce` defaults the switch's `produce` datasets to "Y". With
     data still missing (no switch), the menu prints the preflight message
     and returns 1 before any Docker prompt.
  7. **`punch run <name>`** (`__main__.cmd_run`), single-workflow selector
     only: `offer_producer_switch` (with `menu.choose_producer`) runs before
     the collision check and any Docker call. On a switch:
     - The producer runs with exactly the switch's `produce`; the consumer's
       `--produce` and `--data` are dropped with one
       `[punch] ignoring --produce/--data for <consumer>` line when present.
     - Collision checks, log path (`k6-<producer>.log`), and evidence all use
       the producer.
     - The evidence result carries `"switchedFrom": ["<consumer>", ...]`;
       `tests` lists the workflow that actually ran.
  8. After a switched run passes, both entry points print
     `[punch] <dataset> ready; run <consumer> next.` (the originally selected
     consumer; re-running it walks any remaining hops).
  9. Cancelling, non-TTY input, or no producer leaves today's behavior: the
     preflight message and exit code 1, with no Docker call.
  10. Parent repository workflow YAML marks `http-cart`'s `carts` product
      `recommended: true` (HTTP is faster than the browser producer
      `browser-cart`).

- **Acceptance criteria**
  - Consumer with present data: no picker, behavior identical to today.
  - Consumer with missing data, TTY, pick → exactly one `docker compose run`
    for the picked producer, `--produce <dataset>` effective, consumer never
    launched.
  - Picker lists every compatible producer; recommended row labeled and
    preselected; arrow keys can pick a non-recommended one.
  - No recommended producer → no label, cursor on first row.
  - Two-level chain (`orders-status` ← `orders` ← `carts`) shows two pickers
    and runs only the root producer.
  - Esc / non-TTY → exit 1, zero Docker calls, existing message.
  - Cycle → `None`, cycle line printed, no Docker call.
  - Non-boolean `recommended` → `WorkflowError`; conflicting recommendations
    → `CatalogError`.
  - `punch-run.json` contains `switchedFrom` on a switched run, absent
    otherwise.
  - `all` selector: no picker.
  - No workflow or dataset literal added to `src/punch/*.py`.

- **Testing** — `unittest` in `tests/`: `test_workflow.py` (`recommended`
  parse/default/type), `test_catalog.py` (recommended lookup, conflict),
  `test_execution.py` (missing datasets; switch walk with a scripted fake
  `choose`: present/cancel/pick/chain/cycle/overrides), `test_cli.py` and
  `test_menu.py` (real `choose_producer` with `TerminalMenu` stubbed: rows,
  label, `cursor_index`, switch run, cancel, evidence, `all`). Docker is
  stubbed; tests assert the compose-run count.

- **Docs** — `AGENTS.md` key-patterns line on interactive prompts gains the
  producer picker; `README.md` and `docs/workflows/validation.md` describe
  `recommended`, the picker, and `switchedFrom`; the parent repository's
  `tests/performance/k6/README.md` mentions both.

- **Risks**
  - A switched run writes a dataset the user did not name on the command
    line. Mitigated: only after an explicit pick, only in a TTY.
  - Chains could surprise users. Mitigated: each hop is its own picker.

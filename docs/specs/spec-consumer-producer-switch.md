# Spec — Consumer data preflight with producer switch

> **Status:** Draft for human approval

- **Goal** — When a consumer workflow is selected and a required dataset has
  no data rows, Punch says so *before* any Docker prompt or Docker call and,
  in an interactive terminal, offers to switch to the recommended producer
  workflow. Accepting continues the normal flow with the producer in place of
  the consumer. Every invocation still launches at most one
  `docker compose run`.

- **Design principles**
  - Workflow specifics live only in workflow YAML (`spec.data.produces`,
    `targets`, `requires`). Punch derives producers, recommendations, and
    chains from the catalog; no workflow or dataset name appears in Python.
  - One shared, parameterized implementation serves every entry point. Entry
    points only wire inputs (catalog, overrides, stdin/stdout) and act on the
    returned value.
  - `execute_workflow` stays non-interactive. Its preflight remains as a
    backstop for direct API callers.

- **Non-goals**
  - Running more than one `docker compose run` per invocation. The consumer
    is never run after the producer; the user re-runs it.
  - Row-count thresholds (`minRows`, comparing rows to `ITERATIONS`). "Enough
    data" keeps its current meaning: at least one data row.
  - Optional datasets (`spec.data.optional`). They never trigger a switch.
  - The playground's `./dev perf:*` glue (`scripts/pg/k6runner.py`) —
    deprecated, untouched.
  - New YAML schema fields.
  - Switching for the `all` selector.

- **Definitions**
  - *Missing dataset* — a dataset in `spec.data.requires` whose resolved path
    (default or `--data` override) is absent or has no rows after the header.
    Same rule `preflight_requirements` uses today.
  - *Recommended producers* of dataset `D` for consumer `C` — catalog
    workflows that produce `D` **and** list `C` in that product's `targets`.
    When none target `C`, every producer of `D`. Sorted by name.

- **Functional requirements**
  1. `catalog.WorkflowCatalog.recommended_producers(dataset, consumer) ->
     tuple[str, ...]` implements the definition above. Pure; no I/O.
  2. `execution.missing_datasets(workflow, overrides) -> tuple[str, ...]`
     returns missing required datasets in declaration order. Pure.
     `preflight_requirements` is reimplemented on top of it; its message text
     is unchanged except that it names recommended producers.
  3. `execution.offer_producer_switch(workflow, catalog, overrides, *, stdin,
     stdout) -> ProducerSwitch | None`, where `ProducerSwitch` is a frozen
     dataclass `(workflow: K6Workflow, produce: tuple[str, ...],
     switched_from: tuple[str, ...])`:
     - Returns `None` immediately when nothing is missing.
     - Non-TTY stdin: returns `None` without prompting (caller falls through to
       the existing preflight failure).
     - Otherwise prompts for the first missing dataset:
       `"<consumer>" requires "<dataset>" (no rows at <path>). Switch to
       producer <name> and write it? [y/N]`. With several recommended
       producers, a numbered list and `Pick producer [1-N, Enter=cancel]`.
     - Declining returns `None`.
     - Accepting makes the chosen producer the current workflow with
       `produce=(dataset,)`, and repeats the check on it. A producer that is
       itself a consumer with missing data (e.g. `orders` ← `carts`) is offered
       its own producer in turn. The walk stops at the first workflow whose
       required data is present.
     - A workflow already visited in the walk ends it with `None` and a
       `[punch] producer cycle: a → b → a` line.
     - `switched_from` lists the workflows left behind, in order.
     - The producer's data check ignores the consumer's `--data` overrides.
  4. **`punch` menu** (`menu._run_workflow_menu`): `offer_producer_switch`
     runs right after workflow selection, before base URL, options, produce,
     and Docker prompts. On a switch, every later step uses the producer, and
     `_choose_produce` takes the switch's `produce` as preselected defaults
     (those datasets default to "Y"). On `None` with data still missing, the
     menu prints the preflight message and returns 1 before any Docker prompt.
  5. **`punch run <name>`** (`__main__.cmd_run`), single-workflow selector
     only: `offer_producer_switch` runs before the collision check and any
     Docker call. On a switch:
     - The producer runs with exactly the switch's `produce`;
       the consumer's `--produce` and `--data` are dropped with one
       `[punch] ignoring --produce/--data for <consumer>` line when present.
     - Collision checks, log path (`k6-<producer>.log`), and evidence all use
       the producer.
     - The evidence result for that run carries
       `"switchedFrom": ["<consumer>", ...]`; `tests` lists the workflow that
       actually ran.
  6. After a switched run passes, both entry points print one hint:
     `[punch] <dataset> ready; run <consumer> next.` (the original consumer).
  7. Declining, non-TTY input, or no producer leaves today's behavior: the
     preflight message and exit code 1, with no Docker call.

- **Acceptance criteria**
  - Consumer with present data: no prompt, behavior identical to today.
  - Consumer with missing data, TTY, accept → exactly one Popen of
    `docker compose run` for the producer, `--produce <dataset>` effective,
    consumer never launched.
  - Two-level chain (`orders-status` ← `orders` ← `carts`) offers both
    switches and runs only the root producer.
  - Decline / non-TTY → exit 1, zero Docker calls, existing message.
  - Multiple recommended producers → numbered pick; Enter cancels.
  - Producer whose `targets` include the consumer is recommended over one that
    does not.
  - Cycle → `None`, cycle line printed, no Docker call.
  - `punch-run.json` contains `switchedFrom` on a switched run, absent
    otherwise.
  - `all` selector: no prompt.
  - No workflow or dataset literal added to `src/punch/*.py`.

- **Testing** — `unittest` in `tests/` with fixture catalogs under
  `tests/fixtures/`: `test_catalog.py` (recommended producers),
  `test_execution.py` (missing datasets, prompt accept/decline/non-TTY/pick/
  chain/cycle, preflight message), `test_cli.py` (`cmd_run` switch, dropped
  flags, evidence, `all`), `test_menu.py` (switch before base-URL prompt,
  produce preselection, decline exit). Docker is stubbed; tests assert the
  Popen call count.

- **Docs** — `AGENTS.md` key-patterns line on interactive prompts gains the
  producer-switch offer; Punch workflow docs describe the switch and
  recommendation rule; the playground's `tests/performance/k6/README.md`
  mentions it for `punch` users.

- **Risks**
  - A switched run writes a dataset the user did not explicitly name on the
    command line. Mitigated: only after an explicit `y`, and only in a TTY.
  - Chains could surprise users. Mitigated: each hop is its own prompt.

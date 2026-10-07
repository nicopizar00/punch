# Consumer Producer Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a selected consumer workflow is missing required data, Punch
shows (TTY only) an arrow-key list of every compatible producer — the
YAML-recommended one labeled and preselected — before any Docker prompt, and
runs only the picked workflow.

**Architecture:** YAML gains optional `produces[].recommended`. Two pure
helpers (`WorkflowCatalog.recommended_producer`, `execution.missing_datasets`)
plus a stdlib-only switch walk (`execution.offer_producer_switch`) that takes
the picker as a parameter. One terminal picker (`menu.choose_producer`, on the
existing `_select`/`TerminalMenu` helper) is shared by `punch run` and the
`punch` menu. `execute_workflow` stays non-interactive.

**Tech Stack:** Python 3.10+, PyYAML, simple-term-menu, rich; `unittest`.

**Spec:** `docs/specs/spec-consumer-producer-switch.md` (rev 2; this plan sits
beside it like the other `plan-*.md` files).

## Global Constraints

- At most one `docker compose run` per invocation; the consumer never runs after a switch.
- "Enough data" = required dataset file exists with ≥1 row after the header (unchanged).
- Optional datasets (`spec.data.optional`) never trigger a switch.
- No workflow or dataset name literal in `src/punch/*.py`; all from YAML via the catalog.
- Only YAML schema change: optional boolean `spec.data.produces[].recommended` (default `false`).
- Picker only on a TTY; otherwise current preflight failure, exit 1, no Docker call.
- `all` selector never switches.
- Parent repo `scripts/pg/k6runner.py` / `./dev perf:*` untouched.
- `execute_workflow` gains no prompts; its preflight message text is unchanged.
- No AI attribution in commits.

## Review Focus

1. **Test runner with a real TTY on stdin** — `punch run` tests that leave `sys.stdin` unpatched would open the picker from a terminal; expected: CLI tests pin stdin to a non-TTY stream (Task 4 setUp).
2. **Existing menu test that selects a data-starved consumer** — the picker consumes one more `TerminalMenu`; expected: test updated to cancel the picker and still assert no Docker (Task 5).
3. **Producer chain two hops deep** — expected: two pickers, only the root producer runs (Task 3 walk test; Task 4 CLI chain test).
4. **`--data` override pointing at a file with rows** — expected: no picker, consumer runs (Task 3 `test_override_with_rows_is_not_missing`).
5. **Picked producer missing its own required environment (`RUN_ID`)** — expected: existing "missing required environment" failure, evidence still has `switchedFrom` (Task 4 `test_switched_producer_missing_env_reports_switch`).

## Test command

Host Python may lack `rich`; create a scratch venv once:

```bash
cd vendor/punch
python3 -m venv /tmp/punch-venv && /tmp/punch-venv/bin/pip install -q -r requirements.txt
```

`PY` below means `PYTHONPATH=src /tmp/punch-venv/bin/python`. Full suite:
`PY -m unittest discover -s tests -p 'test_*.py'` (baseline 131 OK).

---

### Task 1: `recommended` in the workflow schema

**Files:**
- Modify: `src/punch/workflow.py` (`DataProduct`, `PRODUCT_KEYS`, `_data_spec`)
- Test: `tests/test_workflow.py`

**Interfaces:**
- Produces: `DataProduct.recommended: bool` (default `False`).

- [ ] **Step 1: Write the failing tests** — add to `LoadWorkflowTests`:

```python
    def test_product_recommended_defaults_to_false(self) -> None:
        workflow = load_workflow(self.workflow_path)
        self.assertFalse(workflow.data.produces[0].recommended)

    def test_product_recommended_true_is_loaded(self) -> None:
        self.write_workflow(
            self.workflow_path,
            ("        targets: [order-status]\n",
             "        targets: [order-status]\n        recommended: true\n"),
        )
        self.assertTrue(load_workflow(self.workflow_path).data.produces[0].recommended)

    def test_rejects_non_boolean_recommended(self) -> None:
        self.assertWorkflowError(
            "spec.data.produces\\[0\\].recommended must be a boolean",
            ("        targets: [order-status]\n",
             "        targets: [order-status]\n        recommended: \"yes\"\n"),
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_workflow -v`
Expected: 3 failures/errors (`AttributeError ... recommended`, unknown field `recommended`).

- [ ] **Step 3: Implement** — in `src/punch/workflow.py`:

```python
@dataclass(frozen=True)
class DataProduct:
    dataset: str
    columns: tuple[str, ...]
    targets: tuple[str, ...]
    recommended: bool = False
```

```python
PRODUCT_KEYS = {"dataset", "columns", "targets", "recommended"}
```

Inside the `for index, raw in enumerate(raw_produces):` loop, before
`produces.append(...)`:

```python
        recommended = product.get("recommended", False)
        if not isinstance(recommended, bool):
            raise WorkflowError(f"{field}.recommended must be a boolean")
```

and pass `recommended=recommended,` as the last `DataProduct(...)` argument.

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_workflow -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/workflow.py tests/test_workflow.py
git commit -m "feat(workflow): optional recommended flag on produced datasets"
```

---

### Task 2: `WorkflowCatalog.recommended_producer` + conflict check

**Files:**
- Modify: `src/punch/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `DataProduct.recommended` (Task 1).
- Produces: `WorkflowCatalog.recommended_producer(self, dataset: str, consumer: str) -> str | None`.

- [ ] **Step 1: Write the failing tests** — add to `CatalogTests`:

```python
    def copy_producer(self, name: str) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        (self.root / f"{name}.yaml").write_text(
            text.replace("name: data-producer", f"name: {name}"), encoding="utf-8"
        )

    def test_recommended_producer_is_the_flagged_one(self) -> None:
        self.copy_producer("other-producer")
        self.edit("other-producer.yaml", "targets: [data-consumer]",
                  "targets: [data-consumer]\n        recommended: true")
        catalog = load_catalog(self.root)
        self.assertEqual(catalog.producers_of("carts"), ("data-producer", "other-producer"))
        self.assertEqual(catalog.recommended_producer("carts", "data-consumer"), "other-producer")

    def test_no_recommended_producer_returns_none(self) -> None:
        catalog = load_catalog(self.root)
        self.assertIsNone(catalog.recommended_producer("carts", "data-consumer"))
        self.assertIsNone(catalog.recommended_producer("unknown", "data-consumer"))

    def test_recommendation_only_covers_listed_targets(self) -> None:
        self.edit("data-output.yaml", "targets: [data-consumer]",
                  "targets: [data-consumer]\n        recommended: true")
        catalog = load_catalog(self.root)
        self.assertIsNone(catalog.recommended_producer("carts", "someone-else"))

    def test_two_recommended_producers_for_one_target_fail(self) -> None:
        self.edit("data-output.yaml", "targets: [data-consumer]",
                  "targets: [data-consumer]\n        recommended: true")
        self.copy_producer("other-producer")
        with self.assertRaisesRegex(
            CatalogError,
            '"carts" has more than one recommended producer for data-consumer: '
            "data-producer, other-producer",
        ):
            load_catalog(self.root)
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_catalog -v`
Expected: errors `no attribute 'recommended_producer'`; conflict test fails (no error raised).

- [ ] **Step 3: Implement** — in `WorkflowCatalog`, after `consumers_of`:

```python
    def recommended_producer(self, dataset: str, consumer: str) -> str | None:
        """The producer flagged `recommended` for `consumer`, if any."""
        for name in self.producers_of(dataset):
            product = self.workflows[name].data.product(dataset)
            if product.recommended and consumer in product.targets:
                return name
        return None
```

At the end of `_validate`:

```python
    recommended: dict[tuple[str, str], list[str]] = {}
    for name, workflow in sorted(catalog.workflows.items()):
        if workflow.data is None:
            continue
        for product in workflow.data.produces:
            if product.recommended:
                for target in product.targets:
                    recommended.setdefault((product.dataset, target), []).append(name)
    for (dataset, target), names in recommended.items():
        if len(names) > 1:
            raise CatalogError(
                f'"{dataset}" has more than one recommended producer for {target}: '
                f"{', '.join(names)}"
            )
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_catalog -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): resolve and validate recommended producers"
```

---

### Task 3: Switch walk in `execution.py`

**Files:**
- Modify: `src/punch/execution.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: `WorkflowCatalog.producers_of`, `.recommended_producer` (Task 2), `.workflows`.
- Produces:
  - `missing_datasets(workflow: K6Workflow, overrides: Mapping[str, Path]) -> tuple[str, ...]`
  - `@dataclass(frozen=True) class ProducerChoice: consumer: str; dataset: str; path: Path; producers: tuple[str, ...]; recommended: str | None`
  - `ProducerChooser = Callable[[ProducerChoice], "str | None"]`
  - `@dataclass(frozen=True) class ProducerSwitch: workflow: K6Workflow; produce: tuple[str, ...]; switched_from: tuple[str, ...]`
  - `offer_producer_switch(workflow, catalog, overrides, *, choose: ProducerChooser, stdout: IO[str]) -> ProducerSwitch | None`
  - `switch_hint(switch: ProducerSwitch) -> str` → `"[punch] <produce[0]> ready; run <switched_from[0]> next."`

- [ ] **Step 1: Write the failing tests** — add `load_catalog` and the new
  names to the imports of `tests/test_execution.py`:

```python
from punch.catalog import load_catalog
from punch.execution import (
    ProducerChoice,
    build_compose_run_command,
    execute_workflow,
    missing_datasets,
    offer_producer_switch,
    switch_hint,
)
```

Append a new class:

```python
class ScriptedChooser:
    """Records each ProducerChoice and answers from a script."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.choices: list[ProducerChoice] = []

    def __call__(self, choice: ProducerChoice) -> str | None:
        self.choices.append(choice)
        return self.answers.pop(0)


class ProducerSwitchTests(unittest.TestCase):
    """data-producer → carts → data-consumer; add_second_hop adds
    data-consumer → orders → data-status."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        fixtures = Path(__file__).resolve().parent / "fixtures"
        for name in ("docker-compose.yml", "data-output.yaml", "data-input.yaml"):
            shutil.copy(fixtures / name, self.root / name)
        self.carts = self.root / "data" / "carts.csv"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def add_second_hop(self) -> None:
        self.edit(
            "data-input.yaml",
            "    requires: [carts]",
            "    produces:\n"
            "      - dataset: orders\n"
            "        columns: [orderId]\n"
            "        targets: [data-status]\n"
            "    requires: [carts]",
        )
        text = (self.root / "data-input.yaml").read_text(encoding="utf-8")
        status = text.replace("name: data-consumer", "name: data-status")
        status = status[: status.index("    produces:\n")] + "    requires: [orders]\n"
        (self.root / "data-status.yaml").write_text(status, encoding="utf-8")

    def write_carts(self, text: str) -> None:
        self.carts.parent.mkdir(parents=True, exist_ok=True)
        self.carts.write_text(text, encoding="utf-8")

    def offer(self, name: str, chooser: ScriptedChooser, overrides=None):
        catalog = load_catalog(self.root)
        stdout = io.StringIO()
        switch = offer_producer_switch(
            catalog.workflows[name], catalog, overrides or {},
            choose=chooser, stdout=stdout,
        )
        return switch, stdout.getvalue()

    def test_missing_datasets_lists_required_without_rows(self) -> None:
        consumer = load_catalog(self.root).workflows["data-consumer"]
        self.assertEqual(missing_datasets(consumer, {}), ("carts",))
        self.write_carts("cartId,productId,sid\n\n")
        self.assertEqual(missing_datasets(consumer, {}), ("carts",))
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(missing_datasets(consumer, {}), ())

    def test_optional_dataset_is_never_missing(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "optional: [carts]")
        consumer = load_catalog(self.root).workflows["data-consumer"]
        self.assertEqual(missing_datasets(consumer, {}), ())

    def test_present_data_never_calls_choose(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        chooser = ScriptedChooser()
        switch, _ = self.offer("data-consumer", chooser)
        self.assertIsNone(switch)
        self.assertEqual(chooser.choices, [])

    def test_override_with_rows_is_not_missing(self) -> None:
        alternate = self.root / "data" / "alt.csv"
        alternate.parent.mkdir(parents=True)
        alternate.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        chooser = ScriptedChooser()
        switch, _ = self.offer("data-consumer", chooser, {"carts": alternate})
        self.assertIsNone(switch)
        self.assertEqual(chooser.choices, [])

    def test_choice_lists_every_producer_and_the_recommended_one(self) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        (self.root / "other-producer.yaml").write_text(
            text.replace("name: data-producer", "name: other-producer").replace(
                "targets: [data-consumer]", "targets: [data-consumer]\n        recommended: true"
            ),
            encoding="utf-8",
        )
        chooser = ScriptedChooser("data-producer")
        switch, _ = self.offer("data-consumer", chooser)
        self.assertEqual(
            chooser.choices,
            [ProducerChoice("data-consumer", "carts", self.carts,
                            ("data-producer", "other-producer"), "other-producer")],
        )
        self.assertEqual(switch.workflow.name, "data-producer")

    def test_pick_switches_to_producer(self) -> None:
        switch, _ = self.offer("data-consumer", ScriptedChooser("data-producer"))
        self.assertEqual(switch.workflow.name, "data-producer")
        self.assertEqual(switch.produce, ("carts",))
        self.assertEqual(switch.switched_from, ("data-consumer",))
        self.assertEqual(switch_hint(switch), "[punch] carts ready; run data-consumer next.")

    def test_cancel_returns_none(self) -> None:
        switch, _ = self.offer("data-consumer", ScriptedChooser(None))
        self.assertIsNone(switch)

    def test_chain_walks_to_root_producer(self) -> None:
        self.add_second_hop()
        chooser = ScriptedChooser("data-consumer", "data-producer")
        switch, _ = self.offer("data-status", chooser)
        self.assertEqual([c.consumer for c in chooser.choices], ["data-status", "data-consumer"])
        self.assertEqual(switch.workflow.name, "data-producer")
        self.assertEqual(switch.produce, ("carts",))
        self.assertEqual(switch.switched_from, ("data-status", "data-consumer"))
        self.assertEqual(switch_hint(switch), "[punch] carts ready; run data-status next.")

    def test_chain_stops_at_first_workflow_with_data(self) -> None:
        self.add_second_hop()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        switch, _ = self.offer("data-status", ScriptedChooser("data-consumer"))
        self.assertEqual(switch.workflow.name, "data-consumer")
        self.assertEqual(switch.produce, ("orders",))
        self.assertEqual(switch.switched_from, ("data-status",))

    def test_cancel_on_second_hop_returns_none(self) -> None:
        self.add_second_hop()
        switch, _ = self.offer("data-status", ScriptedChooser("data-consumer", None))
        self.assertIsNone(switch)

    def test_cycle_returns_none_and_reports(self) -> None:
        self.add_second_hop()
        self.edit("data-output.yaml", "    produces:", "    requires: [orders]\n    produces:")
        self.edit("data-input.yaml", "targets: [data-status]",
                  "targets: [data-status, data-producer]")
        chooser = ScriptedChooser("data-consumer", "data-producer", "data-consumer")
        switch, output = self.offer("data-status", chooser)
        self.assertIsNone(switch)
        self.assertIn(
            "[punch] producer cycle: data-status → data-consumer → data-producer → data-consumer",
            output,
        )

    def test_producer_check_ignores_consumer_overrides(self) -> None:
        self.add_second_hop()
        empty = self.root / "data" / "empty-orders.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("orderId\n", encoding="utf-8")
        chooser = ScriptedChooser("data-consumer", "data-producer")
        switch, _ = self.offer("data-status", chooser, {"orders": empty})
        self.assertEqual(chooser.choices[1].path, self.carts)
        self.assertEqual(switch.workflow.name, "data-producer")
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_execution -v`
Expected: import error `cannot import name 'ProducerChoice'`

- [ ] **Step 3: Implement** — in `src/punch/execution.py`:

Imports:

```python
from typing import IO, TYPE_CHECKING, Callable, Mapping, Sequence
```

after the `from punch.workflow import ...` line:

```python
if TYPE_CHECKING:
    from punch.catalog import WorkflowCatalog
```

After the `ExecutionResult` dataclass:

```python
@dataclass(frozen=True)
class ProducerChoice:
    """One picker question: which producer should write `dataset`?"""

    consumer: str
    dataset: str
    path: Path
    producers: tuple[str, ...]
    recommended: str | None


ProducerChooser = Callable[[ProducerChoice], "str | None"]


@dataclass(frozen=True)
class ProducerSwitch:
    """The workflow to run instead of a consumer whose required data is missing."""

    workflow: K6Workflow
    produce: tuple[str, ...]
    switched_from: tuple[str, ...]
```

Replace `preflight_requirements` with:

```python
def missing_datasets(workflow: K6Workflow, overrides: Mapping[str, Path]) -> tuple[str, ...]:
    """Required datasets whose file is absent or has no data rows."""
    return tuple(
        dataset
        for dataset, path in required_data_paths(workflow, overrides).items()
        if not _has_data_rows(path)
    )


def preflight_requirements(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    producers_of: Callable[[str], Sequence[str]],
) -> str | None:
    for dataset in missing_datasets(workflow, overrides):
        producers = ", ".join(producers_of(dataset)) or "no known workflow"
        return (
            f'{workflow.name} requires "{dataset}"; '
            f"produce it with: {producers} (--produce {dataset})"
        )
    return None


def offer_producer_switch(
    workflow: K6Workflow,
    catalog: WorkflowCatalog,
    overrides: Mapping[str, Path],
    *,
    choose: ProducerChooser,
    stdout: IO[str],
) -> ProducerSwitch | None:
    """Walk from a consumer with missing data to a producer whose own data is
    present, one picked hop at a time. Producers and the recommendation come
    from the catalog, i.e. from workflow YAML."""
    current, current_overrides = workflow, overrides
    visited = [workflow.name]
    produce: tuple[str, ...] = ()
    while missing := missing_datasets(current, current_overrides):
        dataset = missing[0]
        producers = catalog.producers_of(dataset)
        if not producers:
            return None
        chosen = choose(ProducerChoice(
            consumer=current.name,
            dataset=dataset,
            path=required_data_paths(current, current_overrides)[dataset],
            producers=producers,
            recommended=catalog.recommended_producer(dataset, current.name),
        ))
        if chosen is None:
            return None
        if chosen in visited:
            stdout.write(f"[punch] producer cycle: {' → '.join([*visited, chosen])}\n")
            return None
        visited.append(chosen)
        current, current_overrides, produce = catalog.workflows[chosen], {}, (dataset,)
    if current is workflow:
        return None
    return ProducerSwitch(current, produce, tuple(visited[:-1]))


def switch_hint(switch: ProducerSwitch) -> str:
    return f"[punch] {switch.produce[0]} ready; run {switch.switched_from[0]} next."
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_execution tests.test_catalog tests.test_workflow -v`
Expected: all OK (existing preflight tests unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/punch/execution.py tests/test_execution.py
git commit -m "feat(execution): walk a data-starved consumer to a picked producer"
```

---

### Task 4: Shared picker + `punch run` integration

**Files:**
- Modify: `src/punch/menu.py` (add `choose_producer`)
- Modify: `src/punch/__main__.py` (`_evidence_result`, `cmd_run`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `offer_producer_switch`, `switch_hint`, `ProducerChoice`, `ProducerSwitch` (Task 3); `menu._select`, `_MenuCancelled`, `_MenuUnavailable` (existing).
- Produces: `menu.choose_producer(choice: ProducerChoice) -> str | None`; evidence key `"switchedFrom": list[str]` on switched runs only.

- [ ] **Step 1: Write the failing tests** — in `tests/test_cli.py`:

Add `import io` and `from contextlib import contextmanager` to imports. After
`PLAIN_WORKFLOW` add:

```python
class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class SelectedMenu:
    def __init__(self, index: int | None) -> None:
        self.index = index

    def show(self) -> int | None:
        return self.index
```

End of `CliTests.setUp`:

```python
        self.stdin_patch = patch("sys.stdin", io.StringIO())
        self.stdin_patch.start()
```

First line of `tearDown`:

```python
        self.stdin_patch.stop()
```

Add helpers and tests:

```python
    @contextmanager
    def picker(self, *indices: int | None):
        calls: list[tuple[list[str], dict]] = []
        selections = iter(indices)

        def factory(entries, **kwargs):
            calls.append((list(entries), kwargs))
            return SelectedMenu(next(selections))

        output = io.StringIO()
        with patch("sys.stdin", TtyInput("")), patch("sys.stdout", output):
            with patch("punch.menu.TerminalMenu", side_effect=factory):
                yield calls, output

    def add_recommended_browser_producer(self) -> None:
        text = self.producer_path.read_text(encoding="utf-8")
        (self.flows / "data-browser.yaml").write_text(
            text.replace("name: data-producer", "name: data-browser")
                .replace("/scripts/data-producer.js", "/scripts/data-browser.js"),
            encoding="utf-8",
        )
        self.producer_path.write_text(
            text.replace("targets: [data-consumer]",
                         "targets: [data-consumer]\n        recommended: true"),
            encoding="utf-8",
        )

    def test_picker_lists_producers_with_recommended_preselected(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        self.add_recommended_browser_producer()
        with self.picker(1) as (calls, output):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["data-browser", "data-producer  (recommended)"])
        self.assertEqual(kwargs["cursor_index"], 1)
        self.assertIn('"data-consumer" requires "carts"', kwargs["title"])
        self.assertIn("(Esc cancels)", kwargs["title"])
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/data-producer.js", call)
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"), "cartId,productId,sid\nc,p,s\n"
        )
        evidence = self.evidence()
        self.assertEqual(evidence["tests"], ["data-producer"])
        self.assertEqual(evidence["results"][0]["switchedFrom"], ["data-consumer"])
        self.assertIn("[punch] carts ready; run data-consumer next.", output.getvalue())

    def test_arrow_keys_can_pick_a_non_recommended_producer(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        self.add_recommended_browser_producer()
        with self.picker(0):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/data-browser.js", call)

    def test_no_recommendation_starts_on_first_row_without_label(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        with self.picker(0) as (calls, _):
            main(["run", str(self.consumer_path)])
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["data-producer"])
        self.assertEqual(kwargs["cursor_index"], 0)

    def test_cancelled_picker_keeps_preflight_failure(self) -> None:
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("produce it with: data-producer (--produce carts)", result["failure"])
        self.assertNotIn("switchedFrom", result)

    def test_switch_drops_consumer_flags_with_note(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        empty = self.flows / "data" / "empty.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("cartId,productId,sid\n", encoding="utf-8")
        with self.picker(0) as (_, output):
            rc = main(["run", str(self.consumer_path), "--data", "carts=data/empty.csv"])
        self.assertEqual(rc, 0)
        self.assertIn("[punch] ignoring --produce/--data for data-consumer", output.getvalue())
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_switched_producer_missing_env_reports_switch(self) -> None:
        with self.picker(0):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("missing required environment: RUN_ID", result["failure"])
        self.assertEqual(result["switchedFrom"], ["data-consumer"])

    def test_consumer_with_data_opens_no_picker(self) -> None:
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertNotIn("switchedFrom", self.evidence()["results"][0])

    def test_all_selector_never_offers_a_switch(self) -> None:
        with patch("punch.__main__.offer_producer_switch") as offer:
            main(["run", "all"])
        offer.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_cli -v`
Expected: new tests FAIL (no picker call / no `switchedFrom`); `test_all_selector...` errors (no attribute `offer_producer_switch` on `punch.__main__`).

- [ ] **Step 3a: Implement the picker** — in `src/punch/menu.py`, add
  `ProducerChoice` to the `from punch.execution import (...)` list, then add
  after `_choose_workflow`:

```python
def choose_producer(choice: ProducerChoice) -> Optional[str]:
    """Arrow-key list of every compatible producer; the recommended one is
    labeled and preselected. Esc or no terminal returns None."""
    rows = [
        f"{name}  (recommended)" if name == choice.recommended else name
        for name in choice.producers
    ]
    title = (
        f'"{choice.consumer}" requires "{choice.dataset}" (no rows at {choice.path}). '
        "Run a producer instead? (Esc cancels)"
    )
    cursor = (
        choice.producers.index(choice.recommended)
        if choice.recommended in choice.producers
        else 0
    )
    try:
        return choice.producers[_select(rows, title, cursor_index=cursor)]
    except (_MenuCancelled, _MenuUnavailable):
        return None
```

- [ ] **Step 3b: Implement `punch run`** — in `src/punch/__main__.py`, add a
  module-level import (stdlib-only module, safe at import time; lets tests
  patch it):

```python
from punch.execution import offer_producer_switch, switch_hint
```

Extend `_evidence_result`:

```python
def _evidence_result(
    workflow, result, *, skipped: bool = False, switched_from: tuple[str, ...] = ()
) -> dict:
    return {
        # ...existing keys unchanged...
        **({"skipped": True} if skipped else {}),
        **({"switchedFrom": list(switched_from)} if switched_from else {}),
    }
```

In `cmd_run`, after the `runnable` loop and **before** `protected_paths`:

```python
    produce_args, data_args = list(args.produce), list(args.data)
    switch = None
    if args.selector != "all" and runnable:
        from punch.menu import choose_producer

        consumer = runnable[0]
        try:
            switch = offer_producer_switch(
                consumer,
                load_catalog(consumer.source_path.parent),
                resolve_data_overrides(consumer, data_args),
                choose=choose_producer,
                stdout=sys.stdout,
            )
        except (CatalogError, ValueError):
            switch = None  # reported by the per-workflow loop below
        if switch is not None:
            if produce_args or data_args:
                print(f"[punch] ignoring --produce/--data for {consumer.name}", flush=True)
            produce_args, data_args = list(switch.produce), []
            workflows = runnable = [switch.workflow]
    switched_from = switch.switched_from if switch is not None else ()
```

Below that point in `cmd_run`, replace `args.produce` → `produce_args` and
`args.data` → `data_args` (collision loop and per-workflow loop; the early
`all` guard keeps `args.*`). Pass `switched_from=switched_from` to every
`_evidence_result(workflow, ...)` call in the per-workflow loop. After the
executed workflow's `results.append(...)`:

```python
        if result.passed and switch is not None:
            print(switch_hint(switch), flush=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_cli tests.test_execution tests.test_catalog tests.test_workflow -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py src/punch/__main__.py tests/test_cli.py
git commit -m "feat(run): pick a producer when a consumer's data is missing"
```

---

### Task 5: `punch` menu integration

**Files:**
- Modify: `src/punch/menu.py` (`_choose_produce`, `_run_workflow_menu`, imports)
- Test: `tests/test_menu.py`

**Interfaces:**
- Consumes: `choose_producer` (Task 4); `offer_producer_switch`, `preflight_requirements`, `switch_hint` (Task 3).
- Produces: `_choose_produce(workflow: K6Workflow, preselected: Sequence[str] = ()) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_menu.py`:

Give `write_workflow` a `recommended: list[str] | None = None` keyword; in the
`produces` loop append `"        recommended: true\n"` after the `targets`
line when `dataset in (recommended or [])`.

Add a recording variant of `select_menu`:

```python
    @contextmanager
    def record_menus(self, *indices: int | None, answers: str = "yes\n"):
        calls: list[tuple[list[str], dict]] = []
        selections = iter(indices)

        def factory(entries, **kwargs):
            calls.append((list(entries), kwargs))
            return _SelectedMenu(next(selections))

        with patch("sys.stdin", _ScriptedTerminal(answers)):
            with patch("punch.menu.TerminalMenu", side_effect=factory):
                yield calls
```

and next to `_ConfirmedTerminal`:

```python
class _ScriptedTerminal(io.StringIO):
    def isatty(self) -> bool:
        return True
```

Replace `test_consumer_without_data_fails_before_docker` (the picker now
consumes a third menu; `None` = Esc):

```python
    def test_consumer_without_data_fails_before_docker(self) -> None:
        self.write_pair()
        stderr = io.StringIO()
        with self.select_menu(0, 0, None), patch("sys.stderr", stderr):
            rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn(
            'consumer requires "orders"; produce it with: producer (--produce orders)',
            stderr.getvalue(),
        )
```

Add:

```python
    def test_switch_picks_producer_before_base_url_and_preselects_produce(self) -> None:
        self.write_workflow("a-producer", produces={"orders": ["consumer"]})
        self.write_workflow("consumer", requires=["orders"])
        self.write_workflow("producer", forward=["BASE_URL"],
                            produces={"orders": ["consumer"]}, recommended=["orders"])
        output = io.StringIO()
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[DATA orders] 1"}):
            # top-level, workflow (consumer=1), producer picker (producer=1), base URL (0)
            with self.record_menus(0, 1, 1, 0) as calls, patch("sys.stdout", output):
                with patch("builtins.input", return_value="") as prompt:
                    rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        picker_entries, picker_kwargs = calls[2]
        self.assertEqual(picker_entries, ["a-producer", "producer  (recommended)"])
        self.assertEqual(picker_kwargs["cursor_index"], 1)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/producer.js", call)
        self.assertEqual(
            (self.root / "data" / "orders.csv").read_text(encoding="utf-8"), "id\n1\n"
        )
        self.assertIn('Write "orders" data for consumer? (Y/n)',
                      prompt.call_args_list[0].args[0])
        self.assertIn("[punch] orders ready; run consumer next.", output.getvalue())

    def test_consumer_with_data_opens_no_picker(self) -> None:
        self.write_pair()
        (self.root / "data").mkdir()
        (self.root / "data" / "orders.csv").write_text("id\n1\n", encoding="utf-8")
        with self.record_menus(0, 0) as calls:
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/consumer.js", call)
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_menu -v`
Expected: switch test FAIL (consumer runs, no picker); updated cancel test errors/FAILS (`StopIteration` or missing stderr message).

- [ ] **Step 3: Implement** — in `src/punch/menu.py`:

Imports:

```python
from typing import List, Optional, Sequence
```

and add `offer_producer_switch`, `preflight_requirements`, `switch_hint` to
the `from punch.execution import (...)` list.

`_choose_produce`:

```python
def _choose_produce(workflow: K6Workflow, preselected: Sequence[str] = ()) -> tuple[str, ...]:
    if workflow.data is None:
        return ()
    chosen = []
    for product in workflow.data.produces:
        selected = product.dataset in preselected
        answer = _prompt(
            f'Write "{product.dataset}" data for {", ".join(product.targets)}? '
            f'({"Y/n" if selected else "y/N"})',
            default="y" if selected else "n",
        )
        if answer.lower().startswith("y"):
            chosen.append(product.dataset)
    return tuple(chosen)
```

In `_run_workflow_menu`, right after the `try/except` that loads `workflow`
and `catalog`:

```python
    switch = offer_producer_switch(
        workflow, catalog, {}, choose=choose_producer, stdout=sys.stdout
    )
    if switch is not None:
        workflow = switch.workflow
    failure = preflight_requirements(workflow, {}, catalog.producers_of)
    if failure is not None:
        print(f"[punch] {failure}", file=sys.stderr)
        return 1
```

Change `produce = _choose_produce(workflow)` to
`produce = _choose_produce(workflow, switch.produce if switch else ())`.
Inside `if result.passed:` after `_print_metrics(workflow)`:

```python
        if switch is not None:
            print(switch_hint(switch))
```

- [ ] **Step 4: Run full suite**

Run: `PY -m unittest discover -s tests -p 'test_*.py'`
Expected: all OK (131 baseline + new)

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py tests/test_menu.py
git commit -m "feat(menu): pick a producer before any Docker prompt"
```

---

### Task 6: Docs, literal check, parent YAML + pointer

**Files:**
- Modify: `AGENTS.md:49`, `README.md` (data bullets ~lines 30-60), `docs/workflows/validation.md` (evidence schema ~line 28; data section ~line 65)
- Modify (parent repo): `tests/performance/k6/workflows/http-cart.yaml`, `tests/performance/k6/README.md:123-127`, submodule pointer `vendor/punch`

- [ ] **Step 1: Literal check**

Run: `git diff main -- src/punch | grep -E '^\+.*(carts|orders|http-|browser-)'`
Expected: empty.

- [ ] **Step 2: `AGENTS.md:49`** — replace the line with:

```markdown
- A dataset is written only when the run opts in with `--produce <dataset>`; interactive prompts are limited to that opt-in, the producer picker for a consumer whose required data is missing (TTY only, one Compose run), and the consumed-data delete prompt
```

- [ ] **Step 3: `README.md`** — in the YAML example under `produces`, add
  `        recommended: true   # optional`; after the "A consumer is
  preflighted before Docker…" bullet add:

```markdown
- In a terminal, `punch run <consumer>` and the `punch` menu instead open an
  arrow-key list of every producer of the missing dataset. The producer whose
  product sets `recommended: true` (and lists the consumer in `targets`) is
  labeled and preselected. Picking one runs it with `--produce <dataset>` —
  still one Compose run — and walks further when that producer is itself
  missing data; re-run the consumer afterwards. Esc or a non-interactive run
  keeps failing before Docker. Two recommended producers of one dataset for
  the same target fail catalog loading.
```

- [ ] **Step 4: `docs/workflows/validation.md`** — in the evidence schema
  per-result keys add
  `"switchedFrom": ["<consumer>", ...]   // only when a producer pick replaced the selected workflow`;
  in the data section add the Step 3 paragraph.

- [ ] **Step 5: Full suite**

Run: `PY -m unittest discover -s tests -p 'test_*.py'`
Expected: all OK

- [ ] **Step 6: Commit (submodule)**

```bash
git add AGENTS.md README.md docs/workflows/validation.md
git commit -m "docs: describe recommended producers and the producer picker"
```

- [ ] **Step 7: Parent repo** — branch first if on `main`. In
  `tests/performance/k6/workflows/http-cart.yaml`, under the `carts` product
  after `targets: [http-orders]` add `        recommended: true`. In
  `tests/performance/k6/README.md`, after "A consumer fails before Docker
  when a required dataset is missing or has no rows, naming the workflows
  that produce it." insert: "Through `punch run` or the `punch` menu in a
  terminal, Punch instead lists every producer (the `recommended: true` one
  preselected — `http-cart` for `carts`), runs the picked one with
  `--produce`, and tells you to re-run the consumer."

Verify the real catalog loads:

```bash
PYTHONPATH=vendor/punch/src /tmp/punch-venv/bin/python -c \
  "from pathlib import Path; from punch.catalog import load_catalog; c = load_catalog(Path('tests/performance/k6/workflows')); print(c.producers_of('carts'), c.recommended_producer('carts', 'http-orders'))"
```

Expected: `('browser-cart', 'http-cart') http-cart`

```bash
git add vendor/punch tests/performance/k6/workflows/http-cart.yaml tests/performance/k6/README.md
git commit -m "feat(perf): recommend http-cart for carts; bump punch picker"
```

Push only when asked.

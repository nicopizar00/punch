# Data Source Selection and Producer Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before any Docker prompt, Punch (TTY only) lets the operator pick
each optional dataset's source and, when data the run reads is missing, pick a
producer to run instead — recommended one preselected — still launching one
`docker compose run`.

**Architecture:** YAML gains optional `produces[].recommended`. `execution`
learns `optional_choices` (dataset → read file / use default) end-to-end, a
generic picker contract (`Choice` → index), and one stdlib-only walk
(`plan_data`) that applies the same per-workflow step to the selected
workflow and to each picked producer. `menu.choose` is the only terminal
implementation; `punch run` and the `punch` menu share it.

**Tech Stack:** Python 3.10+, PyYAML, simple-term-menu, rich; `unittest`.

**Spec:** `docs/specs/spec-consumer-producer-switch.md` (rev 3).

## Global Constraints

- At most one `docker compose run` per invocation; a replaced workflow never runs afterward.
- "Has data" = file exists with ≥1 row after the header.
- No workflow or dataset name literal in `src/punch/*.py`; everything from YAML via the catalog.
- Only schema change: optional boolean `spec.data.produces[].recommended` (default `false`). `targets` unchanged and unused by this feature.
- Several recommended producers of one dataset → first by workflow name; no error.
- Pickers only with a TTY stdin; non-TTY and `all` keep today's behavior.
- `optional_choices=None` everywhere means today's automatic rule.
- `execute_workflow` gains no prompts; preflight message text unchanged.
- Parent repo `scripts/pg/k6runner.py` / `./dev perf:*` untouched. No `punch doctor` change.
- No AI attribution in commits.

## Review Focus

1. **Test runner with a real TTY on stdin** — `punch run` tests leaving `sys.stdin` unpatched would open pickers; expected: CLI tests pin stdin to a non-TTY stream (Task 5 setUp).
2. **Existing menu tests that run a workflow with missing required or optional data** — each picker consumes a `TerminalMenu`; expected: those tests updated with the extra selection (Task 6).
3. **Optional dataset chosen as "file" while the file has no rows** — expected: producer picker, not a silent default (Task 4 `test_optional_file_without_rows_offers_producers`).
4. **`--data` override for an optional dataset** — expected: no source picker for it, file used (Task 4 `test_override_skips_source_picker`).
5. **Picked producer missing its own required environment** — expected: existing env failure, evidence still has `switchedFrom` (Task 5 `test_switched_producer_missing_env_reports_switch`).

## Test command

Host Python may lack `rich`; create a scratch venv once:

```bash
cd vendor/punch
python3 -m venv /tmp/punch-venv && /tmp/punch-venv/bin/pip install -q -r requirements.txt
```

`PY` = `PYTHONPATH=src /tmp/punch-venv/bin/python`. Full suite:
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
Expected: 3 failures/errors (no attribute `recommended`; unknown field `recommended`).

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

In the `for index, raw in enumerate(raw_produces):` loop, before
`produces.append(...)`:

```python
        recommended = product.get("recommended", False)
        if not isinstance(recommended, bool):
            raise WorkflowError(f"{field}.recommended must be a boolean")
```

and pass `recommended=recommended,` as the last `DataProduct(...)` argument.

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_workflow -v` — Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/workflow.py tests/test_workflow.py
git commit -m "feat(workflow): optional recommended flag on produced datasets"
```

---

### Task 2: `WorkflowCatalog.recommended_producer`

**Files:**
- Modify: `src/punch/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `DataProduct.recommended` (Task 1).
- Produces: `WorkflowCatalog.recommended_producer(self, dataset: str) -> str | None` — first by name among `producers_of(dataset)` whose product is `recommended`.

- [ ] **Step 1: Write the failing tests** — add to `CatalogTests`:

```python
    def copy_producer(self, name: str, *, recommended: bool = False) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        text = text.replace("name: data-producer", f"name: {name}")
        if recommended:
            text = text.replace("targets: [data-consumer]",
                                "targets: [data-consumer]\n        recommended: true")
        (self.root / f"{name}.yaml").write_text(text, encoding="utf-8")

    def test_recommended_producer_is_the_flagged_one(self) -> None:
        self.copy_producer("other-producer", recommended=True)
        catalog = load_catalog(self.root)
        self.assertEqual(catalog.producers_of("carts"), ("data-producer", "other-producer"))
        self.assertEqual(catalog.recommended_producer("carts"), "other-producer")

    def test_no_recommended_producer_returns_none(self) -> None:
        catalog = load_catalog(self.root)
        self.assertIsNone(catalog.recommended_producer("carts"))
        self.assertIsNone(catalog.recommended_producer("unknown"))

    def test_first_recommended_producer_by_name_wins(self) -> None:
        self.copy_producer("zz-producer", recommended=True)
        self.copy_producer("mm-producer", recommended=True)
        self.assertEqual(load_catalog(self.root).recommended_producer("carts"), "mm-producer")
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_catalog -v`
Expected: errors `no attribute 'recommended_producer'`.

- [ ] **Step 3: Implement** — in `WorkflowCatalog`, after `consumers_of`:

```python
    def recommended_producer(self, dataset: str) -> str | None:
        """First producer, by name, that flags `dataset` as recommended."""
        for name in self.producers_of(dataset):
            if self.workflows[name].data.product(dataset).recommended:
                return name
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_catalog -v` — Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): resolve the recommended producer of a dataset"
```

---

### Task 3: Optional choices through `execution`

**Files:**
- Modify: `src/punch/execution.py` (`used_data_paths`, `absent_optional_datasets`, `data_environment`, `preflight_requirements`, `execute_workflow`; new `missing_datasets`, `data_row_count`)
- Test: `tests/test_execution.py`

**Interfaces:**
- Produces (all `optional_choices: Mapping[str, bool] | None = None`, `None` = today's rule; a key's `True` = read the data path, `False` = default):
  - `data_row_count(path: Path) -> int`
  - `used_data_paths(workflow, overrides, optional_choices=None) -> dict[str, Path]`
  - `absent_optional_datasets(workflow, overrides, optional_choices=None) -> tuple[str, ...]`
  - `missing_datasets(workflow, overrides, optional_choices=None) -> tuple[str, ...]`
  - `data_environment(workflow, overrides, optional_choices=None) -> dict[str, str]`
  - `preflight_requirements(workflow, overrides, producers_of, optional_choices=None) -> str | None`
  - `execute_workflow(..., optional_choices: Mapping[str, bool] | None = None, ...)`

- [ ] **Step 1: Update the absent-optional note assertions** — in
  `tests/test_execution.py` replace every `not present` in the three
  optional tests (`test_optional_missing_file_runs_without_env_and_prints_note`,
  `test_optional_header_only_is_absent`,
  `test_optional_override_is_accepted_and_empty_override_is_absent`) with
  `not used`:

```bash
sed -i '' 's/optional dataset "carts" not present/optional dataset "carts" not used/' tests/test_execution.py
```

- [ ] **Step 2: Write the failing tests** — add to `ExecutionTests` (after
  `test_used_data_paths_covers_required_and_present_optional`):

```python
    def run_optional_with(self, choices):
        out = io.StringIO()
        result = execute_workflow(
            self.optional_consumer,
            environment=self.env,
            optional_choices=choices,
            producers_of=lambda dataset: ("data-producer",),
            stdout=out,
            stderr=io.StringIO(),
        )
        return result, out.getvalue()

    def test_optional_choice_default_ignores_present_file(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        result, out = self.run_optional_with({"carts": False})
        self.assertTrue(result.passed, result.failure)
        args = self.args_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(a.startswith("DATA_CARTS_CSV=") for a in args))
        self.assertIn('optional dataset "carts" not used', out)

    def test_optional_choice_file_with_rows_is_injected(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        result, _ = self.run_optional_with({"carts": True})
        self.assertTrue(result.passed, result.failure)
        self.assertIn(
            "DATA_CARTS_CSV=/scripts/data/carts.csv",
            self.args_path.read_text(encoding="utf-8").splitlines(),
        )

    def test_optional_choice_file_without_rows_fails_preflight(self) -> None:
        result, _ = self.run_optional_with({"carts": True})
        self.assertFalse(result.passed)
        self.assertEqual(
            result.failure,
            'data-optional requires "carts"; produce it with: data-producer (--produce carts)',
        )
        self.assertFalse(self.args_path.exists())

    def test_missing_datasets_follow_optional_choices(self) -> None:
        from punch.execution import missing_datasets
        self.assertEqual(missing_datasets(self.consumer, {}), ("carts",))
        self.assertEqual(missing_datasets(self.optional_consumer, {}), ())
        self.assertEqual(missing_datasets(self.optional_consumer, {}, {"carts": True}), ("carts",))
        self.write_carts("cartId,productId,sid\n\n")
        self.assertEqual(missing_datasets(self.consumer, {}), ("carts",))
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(missing_datasets(self.consumer, {}), ())

    def test_data_row_count(self) -> None:
        from punch.execution import data_row_count
        self.assertEqual(data_row_count(self.carts_path), 0)
        self.write_carts("cartId,productId,sid\nc,p,s\n\nd,q,t\n")
        self.assertEqual(data_row_count(self.carts_path), 2)
```

- [ ] **Step 3: Run to verify failure**

Run: `PY -m unittest tests.test_execution -v`
Expected: new tests error (`unexpected keyword argument 'optional_choices'`, import errors); the three edited tests fail (`not used` not found).

- [ ] **Step 4: Implement** — in `src/punch/execution.py`, replace
  `_has_data_rows`, `used_data_paths`, `absent_optional_datasets`,
  `preflight_requirements`, `data_environment` with:

```python
def data_row_count(path: Path) -> int:
    """Non-blank rows after the header; 0 when the file is absent."""
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        next(handle, None)  # header
        return sum(1 for line in handle if line.strip())


def _has_data_rows(path: Path) -> bool:
    return data_row_count(path) > 0


def used_data_paths(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> dict[str, Path]:
    """Required datasets plus optional ones whose choice selects the file
    (no choice: the file has data rows)."""
    used = required_data_paths(workflow, overrides)
    choices = optional_choices or {}
    for dataset, path in optional_data_paths(workflow, overrides).items():
        if choices.get(dataset, _has_data_rows(path)):
            used[dataset] = path
    return used


def absent_optional_datasets(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    used = used_data_paths(workflow, overrides, optional_choices)
    return tuple(d for d in optional_data_paths(workflow, overrides) if d not in used)


def missing_datasets(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    """Datasets this run reads whose file is absent or has no data rows."""
    return tuple(
        dataset
        for dataset, path in used_data_paths(workflow, overrides, optional_choices).items()
        if not _has_data_rows(path)
    )


def preflight_requirements(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    producers_of: Callable[[str], Sequence[str]],
    optional_choices: Mapping[str, bool] | None = None,
) -> str | None:
    for dataset in missing_datasets(workflow, overrides, optional_choices):
        producers = ", ".join(producers_of(dataset)) or "no known workflow"
        return (
            f'{workflow.name} requires "{dataset}"; '
            f"produce it with: {producers} (--produce {dataset})"
        )
    return None


def data_environment(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> dict[str, str]:
    """Container paths of every used dataset, keyed DATA_<NAME>_CSV."""
    if workflow.data is None:
        return {}
    mounted_at = workflow.data.mounted_at.rstrip("/")
    return {
        data_env_name(dataset): f"{mounted_at}/{path.relative_to(workflow.data.directory).as_posix()}"
        for dataset, path in used_data_paths(workflow, overrides, optional_choices).items()
    }
```

In `execute_workflow`: add parameter
`optional_choices: Mapping[str, bool] | None = None,` after `data_overrides`;
pass `optional_choices` to `data_environment(...)`,
`preflight_requirements(...)` (4th argument), and
`absent_optional_datasets(...)`; change the note to:

```python
        output.write(
            f'[punch] optional dataset "{dataset}" not used — scenario uses its default\n'
        )
```

- [ ] **Step 5: Run to verify pass**

Run: `PY -m unittest tests.test_execution -v` — Expected: all OK

- [ ] **Step 6: Commit**

```bash
git add src/punch/execution.py tests/test_execution.py
git commit -m "feat(execution): carry optional dataset choices through a run"
```

---

### Task 4: Generic picker contract and `plan_data` walk

**Files:**
- Modify: `src/punch/execution.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: Task 2 `recommended_producer`; Task 3 `missing_datasets`, `used_data_paths`, `optional_data_paths`, `data_row_count`.
- Produces:
  - `@dataclass(frozen=True) class Choice: title: str; options: tuple[str, ...]; cursor: int = 0`
  - `Chooser = Callable[[Choice], "int | None"]`
  - `@dataclass(frozen=True) class DataPlan: workflow: K6Workflow; overrides: Mapping[str, Path]; optional_choices: Mapping[str, bool]; produce: tuple[str, ...] = (); switched_from: tuple[str, ...] = ()`
  - `plan_data(workflow, catalog, overrides, *, choose: Chooser, stdout: IO[str]) -> DataPlan | None`
  - `switch_hint(plan: DataPlan) -> str`
  - Labels: source picker options `"default (built-in)"` and `"<rel> (<N> rows)"` / `"<rel> (no rows)"`; producer options `"<name>"` / `"<name>  (recommended)"`; `<rel>` = path relative to the workflow's working directory.

- [ ] **Step 1: Write the failing tests** — add imports:

```python
from punch.catalog import load_catalog
from punch.execution import (
    Choice,
    build_compose_run_command,
    execute_workflow,
    plan_data,
    switch_hint,
)
```

Append:

```python
class ScriptedChooser:
    """Records each Choice; answers with an option label (or None = Esc)."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.choices: list[Choice] = []

    def __call__(self, choice: Choice) -> int | None:
        self.choices.append(choice)
        answer = self.answers.pop(0)
        return None if answer is None else choice.options.index(answer)


class PlanDataTests(unittest.TestCase):
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

    def add_recommended_producer(self) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        (self.root / "other-producer.yaml").write_text(
            text.replace("name: data-producer", "name: other-producer").replace(
                "targets: [data-consumer]", "targets: [data-consumer]\n        recommended: true"
            ),
            encoding="utf-8",
        )

    def write_carts(self, text: str) -> None:
        self.carts.parent.mkdir(parents=True, exist_ok=True)
        self.carts.write_text(text, encoding="utf-8")

    def plan(self, name: str, chooser: ScriptedChooser, overrides=None):
        catalog = load_catalog(self.root)
        stdout = io.StringIO()
        plan = plan_data(
            catalog.workflows[name], catalog, overrides or {},
            choose=chooser, stdout=stdout,
        )
        return plan, stdout.getvalue()

    # --- required data ---------------------------------------------------

    def test_present_required_data_asks_nothing(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        chooser = ScriptedChooser()
        plan, _ = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices, [])
        self.assertEqual(plan.workflow.name, "data-consumer")
        self.assertEqual((plan.produce, plan.switched_from), ((), ()))
        self.assertEqual(dict(plan.optional_choices), {})

    def test_producer_picker_lists_all_with_recommended_preselected(self) -> None:
        self.add_recommended_producer()
        chooser = ScriptedChooser("data-producer")
        plan, _ = self.plan("data-consumer", chooser)
        [choice] = chooser.choices
        self.assertEqual(
            choice.title,
            '"data-consumer" needs "carts" (no rows at data/carts.csv). '
            "Run a producer instead? (Esc cancels)",
        )
        self.assertEqual(choice.options, ("data-producer", "other-producer  (recommended)"))
        self.assertEqual(choice.cursor, 1)
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))
        self.assertEqual(plan.switched_from, ("data-consumer",))
        self.assertEqual(dict(plan.overrides), {})
        self.assertEqual(switch_hint(plan), "[punch] carts ready; run data-consumer next.")

    def test_producer_picker_without_recommendation_starts_first(self) -> None:
        chooser = ScriptedChooser("data-producer")
        self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices[0].options, ("data-producer",))
        self.assertEqual(chooser.choices[0].cursor, 0)

    def test_cancel_producer_picker_returns_none(self) -> None:
        plan, _ = self.plan("data-consumer", ScriptedChooser(None))
        self.assertIsNone(plan)

    def test_chain_walks_to_root_producer(self) -> None:
        self.add_second_hop()
        chooser = ScriptedChooser("data-consumer", "data-producer")
        plan, _ = self.plan("data-status", chooser)
        self.assertEqual(len(chooser.choices), 2)
        self.assertTrue(chooser.choices[1].title.startswith('"data-consumer" needs "carts"'))
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))
        self.assertEqual(plan.switched_from, ("data-status", "data-consumer"))
        self.assertEqual(switch_hint(plan), "[punch] carts ready; run data-status next.")

    def test_chain_stops_at_first_workflow_with_data(self) -> None:
        self.add_second_hop()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        plan, _ = self.plan("data-status", ScriptedChooser("data-consumer"))
        self.assertEqual(plan.workflow.name, "data-consumer")
        self.assertEqual(plan.produce, ("orders",))

    def test_cycle_returns_none_and_reports(self) -> None:
        self.add_second_hop()
        self.edit("data-output.yaml", "    produces:", "    requires: [orders]\n    produces:")
        self.edit("data-input.yaml", "targets: [data-status]",
                  "targets: [data-status, data-producer]")
        chooser = ScriptedChooser("data-consumer", "data-producer", "data-consumer")
        plan, output = self.plan("data-status", chooser)
        self.assertIsNone(plan)
        self.assertIn(
            "[punch] producer cycle: data-status → data-consumer → data-producer → data-consumer",
            output,
        )

    def test_producer_step_ignores_selected_workflow_overrides(self) -> None:
        self.add_second_hop()
        empty = self.root / "data" / "empty-orders.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("orderId\n", encoding="utf-8")
        chooser = ScriptedChooser("data-consumer", "data-producer")
        plan, _ = self.plan("data-status", chooser, {"orders": empty})
        self.assertIn("no rows at data/carts.csv", chooser.choices[1].title)
        self.assertEqual(plan.workflow.name, "data-producer")

    # --- optional data ---------------------------------------------------

    def make_optional(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "optional: [carts]")

    def test_source_picker_always_offered_with_default_first(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\nd,q,t\n")
        chooser = ScriptedChooser("default (built-in)")
        plan, _ = self.plan("data-consumer", chooser)
        [choice] = chooser.choices
        self.assertEqual(
            choice.title, '"data-consumer" can read "carts" — pick a source (Esc cancels)'
        )
        self.assertEqual(choice.options, ("default (built-in)", "data/carts.csv (2 rows)"))
        self.assertEqual(choice.cursor, 0)
        self.assertEqual(dict(plan.optional_choices), {"carts": False})
        self.assertEqual(plan.workflow.name, "data-consumer")

    def test_source_picker_file_with_rows(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        plan, _ = self.plan("data-consumer", ScriptedChooser("data/carts.csv (1 rows)"))
        self.assertEqual(dict(plan.optional_choices), {"carts": True})

    def test_optional_file_without_rows_offers_producers(self) -> None:
        self.make_optional()
        chooser = ScriptedChooser("data/carts.csv (no rows)", "data-producer")
        plan, _ = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices[0].options[1], "data/carts.csv (no rows)")
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))

    def test_override_skips_source_picker(self) -> None:
        self.make_optional()
        alternate = self.root / "data" / "alt.csv"
        alternate.parent.mkdir(parents=True)
        alternate.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        chooser = ScriptedChooser()
        plan, _ = self.plan("data-consumer", chooser, {"carts": alternate})
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.optional_choices), {"carts": True})
        self.assertEqual(dict(plan.overrides), {"carts": alternate})

    def test_cancel_source_picker_returns_none(self) -> None:
        self.make_optional()
        plan, _ = self.plan("data-consumer", ScriptedChooser(None))
        self.assertIsNone(plan)
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_execution -v`
Expected: import error `cannot import name 'Choice'`.

- [ ] **Step 3: Implement** — in `src/punch/execution.py`:

Imports:

```python
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Callable, Mapping, Sequence
```

after `from punch.workflow import ...`:

```python
if TYPE_CHECKING:
    from punch.catalog import WorkflowCatalog
```

After `ExecutionResult`:

```python
@dataclass(frozen=True)
class Choice:
    """One picker question; a Chooser answers with an option index or None (Esc)."""

    title: str
    options: tuple[str, ...]
    cursor: int = 0


Chooser = Callable[[Choice], "int | None"]


@dataclass(frozen=True)
class DataPlan:
    """What actually runs: the workflow, its data sources, and any switch."""

    workflow: K6Workflow
    overrides: Mapping[str, Path] = field(default_factory=dict)
    optional_choices: Mapping[str, bool] = field(default_factory=dict)
    produce: tuple[str, ...] = ()
    switched_from: tuple[str, ...] = ()
```

After `preflight_requirements`:

```python
DEFAULT_SOURCE = "default (built-in)"
RECOMMENDED_SUFFIX = "  (recommended)"


def _relative(workflow: K6Workflow, path: Path) -> str:
    try:
        return path.relative_to(workflow.working_directory).as_posix()
    except ValueError:
        return str(path)


def _choose_optional_sources(
    workflow: K6Workflow, overrides: Mapping[str, Path], choose: Chooser
) -> dict[str, bool] | None:
    choices: dict[str, bool] = {}
    for dataset, path in optional_data_paths(workflow, overrides).items():
        if dataset in overrides:
            choices[dataset] = True
            continue
        rows = data_row_count(path)
        file_option = f"{_relative(workflow, path)} ({f'{rows} rows' if rows else 'no rows'})"
        index = choose(Choice(
            title=f'"{workflow.name}" can read "{dataset}" — pick a source (Esc cancels)',
            options=(DEFAULT_SOURCE, file_option),
        ))
        if index is None:
            return None
        choices[dataset] = index == 1
    return choices


def _choose_producer(
    workflow: K6Workflow,
    dataset: str,
    path: Path,
    catalog: WorkflowCatalog,
    choose: Chooser,
) -> str | None:
    producers = catalog.producers_of(dataset)
    if not producers:
        return None
    recommended = catalog.recommended_producer(dataset)
    index = choose(Choice(
        title=(
            f'"{workflow.name}" needs "{dataset}" (no rows at {_relative(workflow, path)}). '
            "Run a producer instead? (Esc cancels)"
        ),
        options=tuple(
            f"{name}{RECOMMENDED_SUFFIX}" if name == recommended else name
            for name in producers
        ),
        cursor=producers.index(recommended) if recommended else 0,
    ))
    return None if index is None else producers[index]


def plan_data(
    workflow: K6Workflow,
    catalog: WorkflowCatalog,
    overrides: Mapping[str, Path],
    *,
    choose: Chooser,
    stdout: IO[str],
) -> DataPlan | None:
    """Settle every data source before Docker: optional-source pickers, then
    preflight, then a producer picker for the first missing dataset. A picked
    producer gets the same step. Everything shown comes from workflow YAML
    through the catalog. None means the operator cancelled."""
    current, current_overrides = workflow, dict(overrides)
    visited = [workflow.name]
    produce: tuple[str, ...] = ()
    while True:
        choices = _choose_optional_sources(current, current_overrides, choose)
        if choices is None:
            return None
        missing = missing_datasets(current, current_overrides, choices)
        if not missing:
            return DataPlan(current, current_overrides, choices, produce, tuple(visited[:-1]))
        dataset = missing[0]
        path = used_data_paths(current, current_overrides, choices)[dataset]
        chosen = _choose_producer(current, dataset, path, catalog, choose)
        if chosen is None:
            return None
        if chosen in visited:
            stdout.write(f"[punch] producer cycle: {' → '.join([*visited, chosen])}\n")
            return None
        visited.append(chosen)
        current, current_overrides, produce = catalog.workflows[chosen], {}, (dataset,)


def switch_hint(plan: DataPlan) -> str:
    return f"[punch] {plan.produce[0]} ready; run {plan.switched_from[0]} next."
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_execution tests.test_catalog tests.test_workflow -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/execution.py tests/test_execution.py
git commit -m "feat(execution): plan data sources and producer switches before Docker"
```

---

### Task 5: `menu.choose` + `punch run` integration

**Files:**
- Modify: `src/punch/menu.py` (add `choose`)
- Modify: `src/punch/__main__.py` (`_evidence_result`, `cmd_run`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4 `Choice`, `DataPlan`, `plan_data`, `switch_hint`; Task 3 `preflight_requirements`, `used_data_paths`, `execute_workflow(optional_choices=...)`; existing `menu._select`, `_MenuCancelled`, `_MenuUnavailable`.
- Produces: `menu.choose(choice: Choice) -> int | None`; evidence key `"switchedFrom": list[str]` on switched runs only; `CANCELED = "data selection canceled"` failure text.

- [ ] **Step 1: Write the failing tests** — in `tests/test_cli.py`:

Add `import io` and `from contextlib import contextmanager`. After
`PLAIN_WORKFLOW`:

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

Helpers + tests:

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

    def make_consumer_optional(self) -> None:
        text = self.consumer_path.read_text(encoding="utf-8")
        self.consumer_path.write_text(
            text.replace("requires: [carts]", "optional: [carts]"), encoding="utf-8"
        )

    def test_producer_picker_switch_runs_producer_once(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        self.add_recommended_browser_producer()
        with self.picker(1) as (calls, output):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["data-browser", "data-producer  (recommended)"])
        self.assertEqual(kwargs["cursor_index"], 1)
        self.assertIn('"data-consumer" needs "carts"', kwargs["title"])
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

    def test_cancelled_producer_picker_keeps_preflight_failure(self) -> None:
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("produce it with: data-producer (--produce carts)", result["failure"])
        self.assertNotIn("switchedFrom", result)

    def test_optional_source_default_runs_without_data_env(self) -> None:
        self.make_consumer_optional()
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        with self.picker(0) as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["default (built-in)", "data/carts.csv (1 rows)"])
        self.assertEqual(kwargs["cursor_index"], 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_optional_source_file_injects_data_env(self) -> None:
        self.make_consumer_optional()
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        with self.picker(1):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", call)

    def test_cancelled_source_picker_fails_before_docker(self) -> None:
        self.make_consumer_optional()
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(self.evidence()["results"][0]["failure"], "data selection canceled")

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

    def test_present_required_data_opens_no_picker(self) -> None:
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertNotIn("switchedFrom", self.evidence()["results"][0])

    def test_non_tty_never_plans(self) -> None:
        with patch("punch.__main__.plan_data") as plan:
            main(["run", str(self.consumer_path)])
        plan.assert_not_called()

    def test_all_selector_never_plans(self) -> None:
        with patch("sys.stdin", TtyInput("")), patch("punch.__main__.plan_data") as plan:
            main(["run", "all"])
        plan.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_cli -v`
Expected: new tests FAIL (no picker / no `switchedFrom`); the two `plan_data` patch tests error (no attribute `plan_data` on `punch.__main__`).

- [ ] **Step 3a: Implement `menu.choose`** — in `src/punch/menu.py` add
  `Choice` to the `from punch.execution import (...)` list; after
  `_choose_workflow`:

```python
def choose(choice: Choice) -> Optional[int]:
    """Render a Choice with the shared arrow-key menu; Esc or no terminal → None."""
    try:
        return _select(list(choice.options), choice.title, cursor_index=choice.cursor)
    except (_MenuCancelled, _MenuUnavailable):
        return None
```

- [ ] **Step 3b: Implement `punch run`** — in `src/punch/__main__.py`, module
  level (stdlib-only module; lets tests patch it):

```python
from punch.execution import DataPlan, plan_data, switch_hint

CANCELED = "data selection canceled"
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

Add to the lazy `from punch.execution import (...)` inside `cmd_run`:
`preflight_requirements`. After the `runnable` loop and **before**
`protected_paths`:

```python
    produce_args, data_args = list(args.produce), list(args.data)
    plan: DataPlan | None = None
    if args.selector != "all" and len(runnable) == 1 and sys.stdin.isatty():
        from punch.menu import choose

        selected = runnable[0]
        try:
            catalog = load_catalog(selected.source_path.parent)
            overrides = resolve_data_overrides(selected, data_args)
        except (CatalogError, ValueError):
            pass  # reported by the per-workflow loop below
        else:
            plan = plan_data(selected, catalog, overrides, choose=choose, stdout=sys.stdout)
            if plan is None:
                failure = (
                    preflight_requirements(selected, overrides, catalog.producers_of)
                    or CANCELED
                )
                print(f"[punch] {failure}", file=sys.stderr, flush=True)
                results.append(_evidence_result(
                    selected, ExecutionResult(selected.name, (), None, False, failure)
                ))
                runnable = []
            elif plan.switched_from:
                if produce_args or data_args:
                    print(f"[punch] ignoring --produce/--data for {selected.name}", flush=True)
                produce_args, data_args = list(plan.produce), []
                workflows = runnable = [plan.workflow]
    switched_from = plan.switched_from if plan is not None else ()
    optional_choices = plan.optional_choices if plan is not None else None
```

Below that point: replace `args.produce` → `produce_args`, `args.data` →
`data_args` (collision loop and per-workflow loop; the early `all` guard keeps
`args.*`). In the per-workflow loop pass `optional_choices=optional_choices`
to `execute_workflow(...)` and as third argument to
`used_data_paths(workflow, overrides, optional_choices)` in the delete prompt;
pass `switched_from=switched_from` to every `_evidence_result(workflow, ...)`
there. After the executed workflow's `results.append(...)`:

```python
        if result.passed and switched_from:
            print(switch_hint(plan), flush=True)
```

Exit code for a cancelled plan: initialize `canceled_rc = 0` just above the
planning block and set `canceled_rc = 1` in the `plan is None` branch. After
the per-workflow loop (which is empty in that case), before
`_write_evidence`, add `overall_rc = overall_rc or canceled_rc`.

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_cli tests.test_execution -v` — Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py src/punch/__main__.py tests/test_cli.py
git commit -m "feat(run): pick data sources and producers before Docker"
```

---

### Task 6: `punch` menu integration

**Files:**
- Modify: `src/punch/menu.py` (`_choose_produce`, `_run_workflow_menu`, imports)
- Test: `tests/test_menu.py`

**Interfaces:**
- Consumes: Task 5 `choose`; Task 4 `plan_data`, `switch_hint`; Task 3 `preflight_requirements`, `data_environment`, `used_data_paths`, `execute_workflow(optional_choices=...)`.
- Produces: `_choose_produce(workflow: K6Workflow, preselected: Sequence[str] = ()) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_menu.py`:

`write_workflow` gets `recommended: list[str] | None = None`; in the
`produces` loop append `"        recommended: true\n"` after the `targets`
line when `dataset in (recommended or [])`.

Next to `_ConfirmedTerminal`:

```python
class _ScriptedTerminal(io.StringIO):
    def isatty(self) -> bool:
        return True
```

Recording helper on `MenuTests`:

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

Replace `test_consumer_without_data_fails_before_docker`:

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

`test_workflow_table_marks_optional_inputs` only inspects the table and
cancels at the second menu — unchanged.

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
        entries, kwargs = calls[2]
        self.assertEqual(entries, ["a-producer", "producer  (recommended)"])
        self.assertEqual(kwargs["cursor_index"], 1)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/producer.js", call)
        self.assertEqual(
            (self.root / "data" / "orders.csv").read_text(encoding="utf-8"), "id\n1\n"
        )
        self.assertIn('Write "orders" data for consumer? (Y/n)',
                      prompt.call_args_list[0].args[0])
        self.assertIn("[punch] orders ready; run consumer next.", output.getvalue())

    def test_optional_source_picker_default_runs_without_data_env(self) -> None:
        self.write_workflow("producer", produces={"orders": ["consumer"]})
        self.write_workflow("consumer", optional=["orders"])
        (self.root / "data").mkdir()
        (self.root / "data" / "orders.csv").write_text("id\n1\n", encoding="utf-8")
        with self.record_menus(0, 0, 0) as calls:
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        entries, kwargs = calls[2]
        self.assertEqual(entries, ["default (built-in)", "data/orders.csv (1 rows)"])
        self.assertEqual(kwargs["cursor_index"], 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_ORDERS_CSV=") for argument in call))

    def test_optional_source_picker_file_injects_data_env(self) -> None:
        self.write_workflow("producer", produces={"orders": ["consumer"]})
        self.write_workflow("consumer", optional=["orders"])
        (self.root / "data").mkdir()
        (self.root / "data" / "orders.csv").write_text("id\n1\n", encoding="utf-8")
        with self.record_menus(0, 0, 1):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("DATA_ORDERS_CSV=/scripts/data/orders.csv", call)

    def test_present_required_data_opens_no_picker(self) -> None:
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
Expected: switch and optional tests FAIL (no picker); updated cancel test errors (`StopIteration`) or FAILs on the stderr message.

- [ ] **Step 3: Implement** — in `src/punch/menu.py`:

```python
from typing import List, Optional, Sequence
```

Add `plan_data`, `preflight_requirements`, `switch_hint` to the
`from punch.execution import (...)` list.

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
    plan = plan_data(workflow, catalog, {}, choose=choose, stdout=sys.stdout)
    if plan is None:
        failure = (
            preflight_requirements(workflow, {}, catalog.producers_of)
            or "data selection canceled"
        )
        print(f"[punch] {failure}", file=sys.stderr)
        return 1
    workflow, choices = plan.workflow, plan.optional_choices
```

Then:
- `produce = _choose_produce(workflow, plan.produce)`
- `data_environment(workflow, {})` → `data_environment(workflow, {}, choices)`
- `execute_workflow(...)`: add `optional_choices=choices,`
- `used_data_paths(workflow, {})` → `used_data_paths(workflow, {}, choices)`
- inside `if result.passed:` after `_print_metrics(workflow)`:

```python
        if plan.switched_from:
            print(switch_hint(plan))
```

- [ ] **Step 4: Run full suite**

Run: `PY -m unittest discover -s tests -p 'test_*.py'`
Expected: all OK (131 baseline + new)

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py tests/test_menu.py
git commit -m "feat(menu): pick data sources and producers before Docker prompts"
```

---

### Task 7: Docs, literal check, parent YAML + pointer

**Files:**
- Modify: `AGENTS.md:49`, `README.md` (data section ~lines 30-60), `docs/workflows/validation.md` (evidence schema ~line 28; data section ~line 65)
- Modify (parent repo): `tests/performance/k6/workflows/http-cart.yaml`, `tests/performance/k6/workflows/http-purchase.yaml`, `tests/performance/k6/README.md:123-127`, submodule pointer `vendor/punch`

- [ ] **Step 1: Literal check**

Run: `git diff main -- src/punch | grep -E '^\+.*(carts|orders|users|http-|browser-)'`
Expected: empty.

- [ ] **Step 2: `AGENTS.md:49`** — replace with:

```markdown
- A dataset is written only when the run opts in with `--produce <dataset>`; interactive prompts are limited to that opt-in, the data-source picker (optional datasets) and producer picker (missing data) shown before Docker on a TTY — still one Compose run — and the consumed-data delete prompt
```

- [ ] **Step 3: `README.md`** — in the YAML example under `produces` add
  `        recommended: true   # optional`; after the "A consumer is
  preflighted before Docker…" bullet add:

```markdown
- In a terminal, `punch run <workflow>` and the `punch` menu settle data
  before Docker. Each optional dataset offers `default (built-in)` or its
  data file (cursor on default). Then the normal preflight runs; when a
  dataset the run reads has no rows, an arrow-key list of every producer of
  it opens, the one whose product sets `recommended: true` labeled and
  preselected (first by name if several). Picking one runs it with
  `--produce <dataset>` — still one Compose run — and walks further when it
  is missing data too; re-run the original workflow afterwards. Esc cancels;
  non-interactive runs keep today's automatic behavior.
```

- [ ] **Step 4: `docs/workflows/validation.md`** — in the evidence schema
  per-result keys add
  `"switchedFrom": ["<workflow>", ...]   // only when a producer pick replaced the selected workflow`;
  in the data section add the Step 3 paragraph.

- [ ] **Step 5: Full suite**

Run: `PY -m unittest discover -s tests -p 'test_*.py'` — Expected: all OK

- [ ] **Step 6: Commit (submodule)**

```bash
git add AGENTS.md README.md docs/workflows/validation.md
git commit -m "docs: describe data source and producer pickers"
```

- [ ] **Step 7: Parent repo** — branch first if on `main`.
  - `tests/performance/k6/workflows/http-cart.yaml`: under the `carts`
    product after `targets: [http-orders]` add `        recommended: true`.
  - `tests/performance/k6/workflows/http-purchase.yaml`: under the `orders`
    product after `targets: [http-orders-status]` add
    `        recommended: true`.
  - `tests/performance/k6/README.md`: after "A consumer fails before Docker
    when a required dataset is missing or has no rows, naming the workflows
    that produce it." insert: "Through `punch run` or the `punch` menu in a
    terminal, Punch first offers each optional dataset's source (built-in
    default or `data/<dataset>.csv`), then — for missing data — lists every
    producer with the `recommended: true` one preselected (`http-cart` for
    `carts`, `http-purchase` for `orders`), runs the picked one with
    `--produce`, and tells you to re-run the original workflow."

Verify the real catalog:

```bash
PYTHONPATH=vendor/punch/src /tmp/punch-venv/bin/python -c \
  "from pathlib import Path; from punch.catalog import load_catalog; c = load_catalog(Path('tests/performance/k6/workflows')); print(c.recommended_producer('carts'), c.recommended_producer('orders'), c.producers_of('orders'))"
```

Expected: `http-cart http-purchase ('browser-purchase', 'http-orders', 'http-purchase')`

```bash
git add vendor/punch tests/performance/k6/workflows/http-cart.yaml \
  tests/performance/k6/workflows/http-purchase.yaml tests/performance/k6/README.md
git commit -m "feat(perf): recommend http producers; bump punch data pickers"
```

Push only when asked.

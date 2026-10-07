# Data Source Selection and Producer Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before any Docker prompt, Punch lets the operator pick each optional
dataset's source and, when data the run reads is missing, pick a producer to
run instead — recommended one preselected — with CLI equivalents for every
choice and still one `docker compose run`.

**Architecture:** YAML gains optional `produces[].recommended`. `execution`
gains data primitives (`optional_choices`, `missing_datasets`,
`data_row_count`, `resolve_data_args`, `data_sources`). A new stdlib-only
`punch.data_plan` module owns the picker contract (`Choice`), the walk
(`plan_data` → `DataPlan | PlanStop`), labels, and the hint. `menu.choose` is
the only terminal picker; `punch run` and the `punch` menu share it.

**Tech Stack:** Python 3.10+, PyYAML, simple-term-menu, rich; `unittest`.

**Spec:** `docs/specs/spec-consumer-producer-switch.md` (rev 4).

## Global Constraints

- At most one `docker compose run` per invocation; a replaced workflow never runs afterward.
- "Has data" = file exists with ≥1 non-blank row after the header.
- No workflow or dataset name literal in `src/punch/*.py`; everything from YAML via the catalog.
- Only schema change: optional boolean `spec.data.produces[].recommended` (default `false`). `targets` unchanged and unused here.
- Several recommended producers of one dataset → first by workflow name; no error.
- Pickers only with a TTY stdin and without `--no-input`; `all` never.
- `optional_choices=None` (or `{}`) everywhere means today's automatic rule.
- `execute_workflow` gains no prompts; preflight message text unchanged.
- `punch.data_plan` imports only stdlib and `punch.*` (no rich / simple-term-menu).
- Parent `scripts/pg/k6runner.py` untouched and must still work. No `punch doctor` change.
- No AI attribution in commits.

## Review Focus

1. **Test runner with a real TTY on stdin** — CLI tests pin stdin to a non-TTY stream (Task 5 setUp) so a terminal-launched suite never opens a picker.
2. **Existing menu tests running a workflow with missing data** — each picker consumes a `TerminalMenu`; the one affected test is updated (Task 6).
3. **Optional file chosen while empty** — expected: producer picker, not silent default (Task 4 `test_optional_file_without_rows_offers_producers`).
4. **`--data` override / `=default` presets in interactive mode** — expected: no source picker for those datasets (Task 4 `test_presets_skip_source_picker`; Task 5 `test_data_default_skips_picker_in_tty`).
5. **Deprecated parent glue keeps working** — expected: `pnpm pg:test` passes and `pg.k6runner` imports after the signature changes (Task 7 Step 8).

## Test command

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

- [ ] **Step 3: Implement** — `src/punch/workflow.py`:

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

- [ ] **Step 4: Run to verify pass** — `PY -m unittest tests.test_workflow -v` → all OK

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
- Produces: `WorkflowCatalog.recommended_producer(self, dataset: str) -> str | None`.

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

- [ ] **Step 2: Run to verify failure** — `PY -m unittest tests.test_catalog -v` → errors `no attribute 'recommended_producer'`.

- [ ] **Step 3: Implement** — in `WorkflowCatalog`, after `consumers_of`:

```python
    def recommended_producer(self, dataset: str) -> str | None:
        """First producer, by name, that flags `dataset` as recommended."""
        for name in self.producers_of(dataset):
            if self.workflows[name].data.product(dataset).recommended:
                return name
        return None
```

- [ ] **Step 4: Run to verify pass** — `PY -m unittest tests.test_catalog -v` → all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): resolve the recommended producer of a dataset"
```

---

### Task 3: Data primitives in `execution`

**Files:**
- Modify: `src/punch/execution.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Produces (`optional_choices: Mapping[str, bool] | None = None`; key `True` = read path, `False` = default, absent = automatic rule):
  - `data_row_count(path: Path) -> int`
  - `used_data_paths(workflow, overrides, optional_choices=None) -> dict[str, Path]`
  - `absent_optional_datasets(workflow, overrides, optional_choices=None) -> tuple[str, ...]`
  - `missing_datasets(workflow, overrides, optional_choices=None) -> tuple[str, ...]`
  - `data_environment(workflow, overrides, optional_choices=None) -> dict[str, str]`
  - `preflight_requirements(workflow, overrides, producers_of, optional_choices=None) -> str | None`
  - `execute_workflow(..., optional_choices: Mapping[str, bool] | None = None, ...)`
  - `DEFAULT_DATA = "default"`
  - `resolve_data_args(workflow, raw: Sequence[str]) -> tuple[dict[str, Path], dict[str, bool]]`
  - `data_sources(workflow, overrides, optional_choices=None) -> dict[str, str]`

- [ ] **Step 1: Update the absent-optional note assertions**

```bash
sed -i '' 's/optional dataset "carts" not present/optional dataset "carts" not used/' tests/test_execution.py
```

- [ ] **Step 2: Write the failing tests** — add to `ExecutionTests` after
  `test_used_data_paths_covers_required_and_present_optional`:

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

    def test_resolve_data_args_splits_default_from_paths(self) -> None:
        from punch.execution import resolve_data_args
        overrides, choices = resolve_data_args(self.optional_consumer, ["carts=default"])
        self.assertEqual((overrides, choices), ({}, {"carts": False}))
        overrides, choices = resolve_data_args(self.optional_consumer, ["carts=data/alt.csv"])
        self.assertEqual(overrides, {"carts": self.root / "data" / "alt.csv"})
        self.assertEqual(choices, {})

    def test_resolve_data_args_rejects_default_for_required(self) -> None:
        from punch.execution import resolve_data_args
        with self.assertRaisesRegex(
            ValueError, '--data carts=default: "carts" is not an optional dataset of data-consumer'
        ):
            resolve_data_args(self.consumer, ["carts=default"])

    def test_data_sources_name_default_or_relative_path(self) -> None:
        from punch.execution import data_sources
        self.assertEqual(data_sources(self.optional_consumer, {}), {"carts": "default"})
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(data_sources(self.optional_consumer, {}), {"carts": "data/carts.csv"})
        self.assertEqual(
            data_sources(self.optional_consumer, {}, {"carts": False}), {"carts": "default"}
        )
        self.assertEqual(data_sources(self.consumer, {}), {"carts": "data/carts.csv"})
        self.assertEqual(data_sources(self.no_csv_workflow, {}), {})
```

- [ ] **Step 3: Run to verify failure** — `PY -m unittest tests.test_execution -v` → new tests error (`unexpected keyword argument 'optional_choices'`, import errors); the three edited tests fail.

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


DEFAULT_DATA = "default"


def resolve_data_args(
    workflow: K6Workflow, raw: Sequence[str]
) -> tuple[dict[str, Path], dict[str, bool]]:
    """Split `--data` into path overrides and `<dataset>=default` choices."""
    paths: list[str] = []
    choices: dict[str, bool] = {}
    for item in raw:
        dataset, separator, value = item.partition("=")
        if separator and value == DEFAULT_DATA:
            if workflow.data is None or dataset not in workflow.data.optional:
                raise ValueError(
                    f'--data {dataset}={DEFAULT_DATA}: "{dataset}" is not an optional '
                    f"dataset of {workflow.name}"
                )
            choices[dataset] = False
        else:
            paths.append(item)
    return resolve_data_overrides(workflow, paths), choices


def data_sources(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> dict[str, str]:
    """Evidence: each declared input's path relative to the working directory,
    or "default" when the scenario's built-in data is used."""
    if workflow.data is None:
        return {}
    used = used_data_paths(workflow, overrides, optional_choices)
    return {
        dataset: (
            used[dataset].relative_to(workflow.working_directory).as_posix()
            if dataset in used
            else DEFAULT_DATA
        )
        for dataset in (*workflow.data.requires, *workflow.data.optional)
    }
```

In `execute_workflow`: add `optional_choices: Mapping[str, bool] | None = None,`
after `data_overrides`; pass it to `data_environment(...)`,
`preflight_requirements(...)` (4th argument), and
`absent_optional_datasets(...)`; change the note to:

```python
        output.write(
            f'[punch] optional dataset "{dataset}" not used — scenario uses its default\n'
        )
```

- [ ] **Step 5: Run to verify pass** — `PY -m unittest tests.test_execution -v` → all OK

- [ ] **Step 6: Commit**

```bash
git add src/punch/execution.py tests/test_execution.py
git commit -m "feat(execution): optional dataset choices, row counts, data sources"
```

---

### Task 4: `punch.data_plan` module

**Files:**
- Create: `src/punch/data_plan.py`
- Test: `tests/test_data_plan.py`

**Interfaces:**
- Consumes: Task 2 `recommended_producer`; Task 3 `data_row_count`, `missing_datasets`, `used_data_paths`; existing `optional_data_paths`, `K6Workflow.required_environment`, `K6Workflow.working_directory`.
- Produces:
  - `CANCELED = "data selection canceled"`, `DEFAULT_SOURCE = "default (built-in)"`
  - `Choice(title: str, options: tuple[str, ...], cursor: int = 0)` (frozen)
  - `Chooser = Callable[[Choice], int | None]`
  - `DataPlan(workflow, overrides={}, optional_choices={}, produce=(), switched_from=())` (frozen)
  - `PlanStop(reason: str, canceled: bool = False)` (frozen)
  - `plan_data(workflow, catalog, overrides, optional_choices, *, choose: Chooser, environment: Mapping[str, str]) -> DataPlan | PlanStop`
  - `switch_hint(plan: DataPlan, catalog: WorkflowCatalog) -> str`

- [ ] **Step 1: Write the failing tests** — create `tests/test_data_plan.py`:

```python
from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.data_plan import (
    CANCELED,
    Choice,
    DataPlan,
    PlanStop,
    plan_data,
    switch_hint,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ENV = {"RUN_ID": "1"}  # data-producer requires RUN_ID


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
        for name in ("docker-compose.yml", "data-output.yaml", "data-input.yaml"):
            shutil.copy(FIXTURES / name, self.root / name)
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

    def make_optional(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "optional: [carts]")

    def write_carts(self, text: str) -> None:
        self.carts.parent.mkdir(parents=True, exist_ok=True)
        self.carts.write_text(text, encoding="utf-8")

    def plan(self, name, chooser, overrides=None, choices=None, environment=ENV):
        catalog = load_catalog(self.root)
        return plan_data(
            catalog.workflows[name], catalog, overrides or {}, choices or {},
            choose=chooser, environment=environment,
        )

    # --- required data ---------------------------------------------------

    def test_present_required_data_asks_nothing(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        chooser = ScriptedChooser()
        plan = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices, [])
        self.assertIsInstance(plan, DataPlan)
        self.assertEqual(plan.workflow.name, "data-consumer")
        self.assertEqual((plan.produce, plan.switched_from), ((), ()))

    def test_producer_picker_lists_all_with_recommended_preselected(self) -> None:
        self.add_recommended_producer()
        chooser = ScriptedChooser("data-producer")
        plan = self.plan("data-consumer", chooser)
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

    def test_producer_tags_missing_required_environment(self) -> None:
        self.add_recommended_producer()
        chooser = ScriptedChooser("data-producer  (needs RUN_ID)")
        self.plan("data-consumer", chooser, environment={})
        self.assertEqual(
            chooser.choices[0].options,
            ("data-producer  (needs RUN_ID)", "other-producer  (recommended, needs RUN_ID)"),
        )

    def test_producer_picker_without_recommendation_starts_first(self) -> None:
        chooser = ScriptedChooser("data-producer")
        self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices[0].options, ("data-producer",))
        self.assertEqual(chooser.choices[0].cursor, 0)

    def test_cancel_producer_picker_stops_with_preflight_detail(self) -> None:
        stop = self.plan("data-consumer", ScriptedChooser(None))
        self.assertEqual(
            stop,
            PlanStop(
                f'{CANCELED}; "data-consumer" needs "carts" (no rows at data/carts.csv) '
                "— produce it with: data-producer (--produce carts)",
                canceled=True,
            ),
        )

    def test_chain_walks_to_root_producer(self) -> None:
        self.add_second_hop()
        chooser = ScriptedChooser("data-consumer", "data-producer")
        plan = self.plan("data-status", chooser)
        self.assertEqual(len(chooser.choices), 2)
        self.assertTrue(chooser.choices[1].title.startswith('"data-consumer" needs "carts"'))
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))
        self.assertEqual(plan.switched_from, ("data-status", "data-consumer"))

    def test_chain_stops_at_first_workflow_with_data(self) -> None:
        self.add_second_hop()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        plan = self.plan("data-status", ScriptedChooser("data-consumer"))
        self.assertEqual(plan.workflow.name, "data-consumer")
        self.assertEqual(plan.produce, ("orders",))

    def test_cycle_stops(self) -> None:
        self.add_second_hop()
        self.edit("data-output.yaml", "    produces:", "    requires: [orders]\n    produces:")
        self.edit("data-input.yaml", "targets: [data-status]",
                  "targets: [data-status, data-producer]")
        chooser = ScriptedChooser("data-consumer", "data-producer", "data-consumer")
        stop = self.plan("data-status", chooser)
        self.assertEqual(
            stop,
            PlanStop("producer cycle: data-status → data-consumer → data-producer → data-consumer"),
        )

    def test_producer_step_ignores_selected_workflow_overrides(self) -> None:
        self.add_second_hop()
        empty = self.root / "data" / "empty-orders.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("orderId\n", encoding="utf-8")
        chooser = ScriptedChooser("data-consumer", "data-producer")
        plan = self.plan("data-status", chooser, overrides={"orders": empty})
        self.assertIn("no rows at data/carts.csv", chooser.choices[1].title)
        self.assertEqual(plan.workflow.name, "data-producer")

    # --- optional data ---------------------------------------------------

    def test_source_picker_with_default_first(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\nd,q,t\n")
        chooser = ScriptedChooser("default (built-in)")
        plan = self.plan("data-consumer", chooser)
        [choice] = chooser.choices
        self.assertEqual(
            choice.title, '"data-consumer" can read "carts" — pick a source (Esc cancels)'
        )
        self.assertEqual(choice.options, ("default (built-in)", "data/carts.csv (2 rows)"))
        self.assertEqual(choice.cursor, 0)
        self.assertEqual(dict(plan.optional_choices), {"carts": False})

    def test_source_picker_file_with_rows(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        plan = self.plan("data-consumer", ScriptedChooser("data/carts.csv (1 rows)"))
        self.assertEqual(dict(plan.optional_choices), {"carts": True})

    def test_optional_file_without_rows_offers_producers(self) -> None:
        self.make_optional()
        chooser = ScriptedChooser("data/carts.csv (no rows)", "data-producer")
        plan = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices[0].options[1], "data/carts.csv (no rows)")
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))

    def test_optional_without_rows_or_producer_defaults_silently(self) -> None:
        self.make_optional()
        (self.root / "data-output.yaml").unlink()
        chooser = ScriptedChooser()
        plan = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.optional_choices), {"carts": False})

    def test_presets_skip_source_picker(self) -> None:
        self.make_optional()
        alternate = self.root / "data" / "alt.csv"
        alternate.parent.mkdir(parents=True)
        alternate.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        chooser = ScriptedChooser()
        plan = self.plan("data-consumer", chooser, overrides={"carts": alternate})
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.overrides), {"carts": alternate})
        plan = self.plan("data-consumer", chooser, choices={"carts": False})
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.optional_choices), {"carts": False})

    def test_cancel_source_picker(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(
            self.plan("data-consumer", ScriptedChooser(None)), PlanStop(CANCELED, canceled=True)
        )

    # --- hint ------------------------------------------------------------

    def test_switch_hint_names_origin_and_what_it_still_misses(self) -> None:
        self.add_second_hop()
        catalog = load_catalog(self.root)
        plan = DataPlan(catalog.workflows["data-producer"], produce=("carts",),
                        switched_from=("data-status", "data-consumer"))
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(
            switch_hint(plan, catalog),
            "[punch] carts ready; run data-status next (still missing: orders).",
        )
        plan = DataPlan(catalog.workflows["data-producer"], produce=("carts",),
                        switched_from=("data-consumer",))
        self.assertEqual(switch_hint(plan, catalog), "[punch] carts ready; run data-consumer next.")


if __name__ == "__main__":
    unittest.main()
```

Note `test_optional_without_rows_or_producer_defaults_silently`: deleting
the only producer is valid for the catalog because an optional dataset needs
no producer.

- [ ] **Step 2: Run to verify failure** — `PY -m unittest tests.test_data_plan -v` → `ModuleNotFoundError: No module named 'punch.data_plan'`.

- [ ] **Step 3: Implement** — create `src/punch/data_plan.py`:

```python
"""Settle every data source a workflow reads before Docker runs.

Standard-library only. Every workflow, dataset, and environment name shown
comes from workflow YAML through the catalog. The picker is a parameter, so
`punch run` and the `punch` menu share one walk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from punch.catalog import WorkflowCatalog
from punch.execution import (
    data_row_count,
    missing_datasets,
    optional_data_paths,
    used_data_paths,
)
from punch.workflow import K6Workflow

CANCELED = "data selection canceled"
DEFAULT_SOURCE = "default (built-in)"


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


@dataclass(frozen=True)
class PlanStop:
    """Why nothing will run; `canceled` when the operator pressed Esc."""

    reason: str
    canceled: bool = False


def _relative(workflow: K6Workflow, path: Path) -> str:
    try:
        return path.relative_to(workflow.working_directory).as_posix()
    except ValueError:
        return str(path)


def _choose_sources(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    preset: Mapping[str, bool],
    catalog: WorkflowCatalog,
    choose: Chooser,
) -> dict[str, bool] | None:
    choices = dict(preset)
    for dataset, path in optional_data_paths(workflow, overrides).items():
        if dataset in choices or dataset in overrides:
            continue
        rows = data_row_count(path)
        if not rows and not catalog.producers_of(dataset):
            choices[dataset] = False  # only the default is available
            continue
        index = choose(Choice(
            title=f'"{workflow.name}" can read "{dataset}" — pick a source (Esc cancels)',
            options=(
                DEFAULT_SOURCE,
                f"{_relative(workflow, path)} ({f'{rows} rows' if rows else 'no rows'})",
            ),
        ))
        if index is None:
            return None
        choices[dataset] = index == 1
    return choices


def _producer_option(
    name: str, recommended: str | None, catalog: WorkflowCatalog, environment: Mapping[str, str]
) -> str:
    tags = ["recommended"] if name == recommended else []
    missing_env = [
        variable for variable in catalog.workflows[name].required_environment
        if not environment.get(variable)
    ]
    if missing_env:
        tags.append(f"needs {', '.join(missing_env)}")
    return f"{name}  ({', '.join(tags)})" if tags else name


def plan_data(
    workflow: K6Workflow,
    catalog: WorkflowCatalog,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool],
    *,
    choose: Chooser,
    environment: Mapping[str, str],
) -> DataPlan | PlanStop:
    """Optional-source pickers, then preflight, then a producer picker for the
    first missing dataset; a picked producer gets the same step."""
    current, current_overrides, preset = workflow, dict(overrides), dict(optional_choices)
    visited = [workflow.name]
    produce: tuple[str, ...] = ()
    while True:
        choices = _choose_sources(current, current_overrides, preset, catalog, choose)
        if choices is None:
            return PlanStop(CANCELED, canceled=True)
        missing = missing_datasets(current, current_overrides, choices)
        if not missing:
            return DataPlan(current, current_overrides, choices, produce, tuple(visited[:-1]))
        dataset = missing[0]
        path = used_data_paths(current, current_overrides, choices)[dataset]
        need = f'"{current.name}" needs "{dataset}" (no rows at {_relative(current, path)})'
        producers = catalog.producers_of(dataset)
        if not producers:
            return PlanStop(f"{need} and no workflow produces it")
        recommended = catalog.recommended_producer(dataset)
        index = choose(Choice(
            title=f"{need}. Run a producer instead? (Esc cancels)",
            options=tuple(
                _producer_option(name, recommended, catalog, environment) for name in producers
            ),
            cursor=producers.index(recommended) if recommended else 0,
        ))
        if index is None:
            return PlanStop(
                f"{CANCELED}; {need} — produce it with: {', '.join(producers)} "
                f"(--produce {dataset})",
                canceled=True,
            )
        chosen = producers[index]
        if chosen in visited:
            return PlanStop(f"producer cycle: {' → '.join([*visited, chosen])}")
        visited.append(chosen)
        current, current_overrides, preset = catalog.workflows[chosen], {}, {}
        produce = (dataset,)


def switch_hint(plan: DataPlan, catalog: WorkflowCatalog) -> str:
    origin = catalog.workflows[plan.switched_from[0]]
    still = missing_datasets(origin, {})
    hint = f"[punch] {plan.produce[0]} ready; run {origin.name} next"
    return f"{hint} (still missing: {', '.join(still)})." if still else f"{hint}."
```

- [ ] **Step 4: Run to verify pass** — `PY -m unittest tests.test_data_plan tests.test_execution tests.test_catalog tests.test_workflow -v` → all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/data_plan.py tests/test_data_plan.py
git commit -m "feat(data-plan): settle data sources and producer switches before Docker"
```

---

### Task 5: `menu.choose` + `punch run` integration

**Files:**
- Modify: `src/punch/menu.py` (add `choose`)
- Modify: `src/punch/__main__.py` (`_evidence_result`, `cmd_run`, `run` parser)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4 `Choice`, `DataPlan`, `PlanStop`, `plan_data`, `switch_hint`; Task 3 `resolve_data_args`, `data_sources`, `used_data_paths`, `execute_workflow(optional_choices=...)`; existing `menu._select`, `_MenuCancelled`, `_MenuUnavailable`.
- Produces: `menu.choose(choice: Choice) -> int | None`; `punch run --no-input`; `--data <dataset>=default`; evidence keys `"switchedFrom"` (switched runs only) and `"dataSources"` (data-reading workflows).

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

    def write_carts_rows(self) -> None:
        self.carts_path.parent.mkdir(parents=True, exist_ok=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")

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

    def test_cancelled_producer_picker_fails_with_reason(self) -> None:
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertTrue(result["failure"].startswith("data selection canceled;"))
        self.assertIn("produce it with: data-producer (--produce carts)", result["failure"])
        self.assertNotIn("switchedFrom", result)

    def test_optional_source_default_runs_without_data_env(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker(0) as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["default (built-in)", "data/carts.csv (1 rows)"])
        self.assertEqual(kwargs["cursor_index"], 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))
        self.assertEqual(self.evidence()["results"][0]["dataSources"], {"carts": "default"})

    def test_optional_source_file_injects_data_env(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker(1):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", call)
        self.assertEqual(
            self.evidence()["results"][0]["dataSources"], {"carts": "data/carts.csv"}
        )

    def test_cancelled_source_picker_fails_before_docker(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(self.evidence()["results"][0]["failure"], "data selection canceled")

    def test_no_input_in_tty_opens_no_picker(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path), "--no-input"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        [call] = self.fake_docker_calls()
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", call)

    def test_data_default_skips_picker_in_tty(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path), "--data", "carts=default"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_data_default_applies_without_tty(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        rc = main(["run", str(self.consumer_path), "--data", "carts=default"])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_data_default_on_required_fails_before_docker(self) -> None:
        rc = main(["run", str(self.consumer_path), "--data", "carts=default"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertIn("is not an optional dataset", self.evidence()["results"][0]["failure"])

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
        with self.picker(0) as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(calls[0][0], ["data-producer  (needs RUN_ID)"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("missing required environment: RUN_ID", result["failure"])
        self.assertEqual(result["switchedFrom"], ["data-consumer"])

    def test_present_required_data_opens_no_picker(self) -> None:
        self.write_carts_rows()
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        result = self.evidence()["results"][0]
        self.assertNotIn("switchedFrom", result)
        self.assertEqual(result["dataSources"], {"carts": "data/carts.csv"})

    def test_workflow_without_data_records_no_data_sources(self) -> None:
        self.assertEqual(main(["run", str(self.workflow_path)]), 0)
        self.assertNotIn("dataSources", self.evidence()["results"][0])

    def test_non_tty_never_plans(self) -> None:
        with patch("punch.__main__.plan_data") as plan:
            main(["run", str(self.consumer_path)])
        plan.assert_not_called()

    def test_all_selector_never_plans(self) -> None:
        with patch("sys.stdin", TtyInput("")), patch("punch.__main__.plan_data") as plan:
            main(["run", "all"])
        plan.assert_not_called()
```

- [ ] **Step 2: Run to verify failure** — `PY -m unittest tests.test_cli -v` → new tests FAIL/ERROR (`unrecognized arguments: --no-input`, no picker, no `dataSources`, no attribute `plan_data`).

- [ ] **Step 3a: `menu.choose`** — in `src/punch/menu.py`, add
  `from punch.data_plan import Choice` to the imports; after
  `_choose_workflow`:

```python
def choose(choice: Choice) -> Optional[int]:
    """Render a Choice with the shared arrow-key menu; Esc or no terminal → None."""
    try:
        return _select(list(choice.options), choice.title, cursor_index=choice.cursor)
    except (_MenuCancelled, _MenuUnavailable):
        return None
```

- [ ] **Step 3b: Parser** — in `src/punch/__main__.py`, after the `--data`
  argument:

```python
    run_p.add_argument("--no-input", action="store_true",
                       help="Never open the data-source or producer pickers.")
```

and change the `--data` help to:
`"Read a dataset from PATH (beneath spec.data.directory), or DATASET=default for an optional dataset's built-in data."`

- [ ] **Step 3c: Module-level imports** — `src/punch/__main__.py` (stdlib-only
  modules; lets tests patch `plan_data`):

```python
from punch.data_plan import DataPlan, PlanStop, plan_data, switch_hint
```

- [ ] **Step 3d: `_evidence_result`**

```python
def _evidence_result(
    workflow,
    result,
    *,
    skipped: bool = False,
    switched_from: tuple[str, ...] = (),
    data_sources: dict[str, str] | None = None,
) -> dict:
    return {
        # ...existing keys unchanged...
        **({"skipped": True} if skipped else {}),
        **({"switchedFrom": list(switched_from)} if switched_from else {}),
        **({"dataSources": data_sources} if data_sources else {}),
    }
```

- [ ] **Step 3e: `cmd_run`** — add `data_sources` and `resolve_data_args` to
  its lazy `from punch.execution import (...)` list. After the `runnable`
  loop and **before** `protected_paths`:

```python
    produce_args, data_args = list(args.produce), list(args.data)
    plan: DataPlan | None = None
    stopped_rc = 0
    if (
        args.selector != "all"
        and len(runnable) == 1
        and not args.no_input
        and sys.stdin.isatty()
    ):
        from punch.menu import choose

        selected = runnable[0]
        try:
            catalog = load_catalog(selected.source_path.parent)
            overrides, preset = resolve_data_args(selected, data_args)
        except (CatalogError, ValueError):
            pass  # reported by the per-workflow loop below
        else:
            outcome = plan_data(
                selected, catalog, overrides, preset, choose=choose, environment=os.environ
            )
            if isinstance(outcome, PlanStop):
                print(f"[punch] {outcome.reason}", file=sys.stderr, flush=True)
                results.append(_evidence_result(
                    selected, ExecutionResult(selected.name, (), None, False, outcome.reason)
                ))
                runnable, stopped_rc = [], 1
            else:
                plan = outcome
                if plan.switched_from:
                    if produce_args or data_args:
                        print(f"[punch] ignoring --produce/--data for {selected.name}", flush=True)
                    produce_args, data_args = list(plan.produce), []
                    workflows = runnable = [plan.workflow]
    switched_from = plan.switched_from if plan is not None else ()
```

Below that point:
- Replace `args.produce` → `produce_args`, `args.data` → `data_args` (collision loop and per-workflow loop; the early `all` guard keeps `args.*`).
- In the per-workflow loop replace
  `overrides = resolve_data_overrides(workflow, args.data)` with
  `overrides, preset = resolve_data_args(workflow, data_args)` and right
  after the `try/except`:
  `choices = plan.optional_choices if plan is not None else preset`.
- `execute_workflow(...)`: add `optional_choices=choices,`.
- `results.append(_evidence_result(workflow, result))` →
  `results.append(_evidence_result(workflow, result, switched_from=switched_from, data_sources=data_sources(workflow, overrides, choices)))`.
- The error-path `_evidence_result(...)` in the `except` also gets
  `switched_from=switched_from`.
- Delete prompt: `used_data_paths(workflow, overrides)` →
  `used_data_paths(workflow, overrides, choices)`.
- After the executed workflow's `results.append(...)`:

```python
        if result.passed and switched_from:
            print(switch_hint(plan, catalog), flush=True)
```

- Before `_write_evidence(...)`: `overall_rc = overall_rc or stopped_rc`.

- [ ] **Step 4: Run to verify pass** — `PY -m unittest tests.test_cli tests.test_data_plan tests.test_execution -v` → all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py src/punch/__main__.py tests/test_cli.py
git commit -m "feat(run): data pickers, --no-input, --data default, data source evidence"
```

---

### Task 6: `punch` menu integration

**Files:**
- Modify: `src/punch/menu.py` (`_choose_produce`, `_run_workflow_menu`, imports)
- Test: `tests/test_menu.py`

**Interfaces:**
- Consumes: Task 5 `choose`; Task 4 `plan_data`, `PlanStop`, `switch_hint`; Task 3 `data_environment`, `used_data_paths`, `execute_workflow(optional_choices=...)`.
- Produces: `_choose_produce(workflow: K6Workflow, forced: Sequence[str] = ()) -> tuple[str, ...]`.

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

Replace `test_consumer_without_data_fails_before_docker` (Esc on the producer
picker now means "menu canceled", exit 0):

```python
    def test_esc_on_producer_picker_cancels_menu_before_docker(self) -> None:
        self.write_pair()
        output = io.StringIO()
        with self.select_menu(0, 0, None), patch("sys.stdout", output):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn("[punch] menu canceled.", output.getvalue())
```

Add:

```python
    def test_switch_picks_producer_before_base_url_and_forces_produce(self) -> None:
        self.write_workflow("a-producer", produces={"orders": ["consumer"]})
        self.write_workflow("consumer", requires=["orders"])
        self.write_workflow("producer", forward=["BASE_URL"],
                            produces={"orders": ["consumer"]}, recommended=["orders"])
        output = io.StringIO()
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[DATA orders] 1"}):
            # top-level, workflow (consumer=1), producer picker (producer=1), base URL (0)
            with self.record_menus(0, 1, 1, 0) as calls, patch("sys.stdout", output):
                with patch("builtins.input", return_value="n") as prompt:
                    rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        entries, kwargs = calls[2]
        self.assertEqual(entries, ["a-producer", "producer  (recommended)"])
        self.assertEqual(kwargs["cursor_index"], 1)
        prompt.assert_not_called()
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/producer.js", call)
        self.assertEqual(
            (self.root / "data" / "orders.csv").read_text(encoding="utf-8"), "id\n1\n"
        )
        text = output.getvalue()
        self.assertIn('[punch] writing "orders" (needed by the selected workflow)', text)
        self.assertIn("[punch] orders ready; run consumer next.", text)

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

    def test_plan_stop_without_cancel_fails_before_docker(self) -> None:
        self.write_workflow("consumer", requires=["orders"])
        self.write_workflow("producer", produces={"orders": ["consumer"]}, requires=["orders"])
        stderr = io.StringIO()
        # consumer=0, producer picker → producer (0), producer needs orders → producer again (0)
        with self.record_menus(0, 0, 0, 0), patch("sys.stderr", stderr):
            rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn("[punch] producer cycle: consumer → producer → producer", stderr.getvalue())
```

- [ ] **Step 2: Run to verify failure** — `PY -m unittest tests.test_menu -v` → new tests FAIL (no picker / no cancel message / no cycle message).

- [ ] **Step 3: Implement** — in `src/punch/menu.py`:

```python
from typing import List, Optional, Sequence
```

```python
from punch.data_plan import Choice, PlanStop, plan_data, switch_hint
```

`_choose_produce`:

```python
def _choose_produce(workflow: K6Workflow, forced: Sequence[str] = ()) -> tuple[str, ...]:
    if workflow.data is None:
        return ()
    chosen = []
    for product in workflow.data.produces:
        if product.dataset in forced:
            print(f'[punch] writing "{product.dataset}" (needed by the selected workflow)')
            chosen.append(product.dataset)
            continue
        answer = _prompt(
            f'Write "{product.dataset}" data for {", ".join(product.targets)}? (y/N)',
            default="n",
        )
        if answer.lower().startswith("y"):
            chosen.append(product.dataset)
    return tuple(chosen)
```

In `_run_workflow_menu`, right after the `try/except` that loads `workflow`
and `catalog`:

```python
    outcome = plan_data(workflow, catalog, {}, {}, choose=choose, environment=os.environ)
    if isinstance(outcome, PlanStop):
        if outcome.canceled:
            raise _MenuCancelled
        print(f"[punch] {outcome.reason}", file=sys.stderr)
        return 1
    plan = outcome
    workflow, choices = plan.workflow, plan.optional_choices
```

Then:
- `produce = _choose_produce(workflow)` → `_choose_produce(workflow, plan.produce)`
- `data_environment(workflow, {})` → `data_environment(workflow, {}, choices)`
- `execute_workflow(...)`: add `optional_choices=choices,`
- `used_data_paths(workflow, {})` → `used_data_paths(workflow, {}, choices)`
- inside `if result.passed:` after `_print_metrics(workflow)`:

```python
        if plan.switched_from:
            print(switch_hint(plan, catalog))
```

- [ ] **Step 4: Run full suite** — `PY -m unittest discover -s tests -p 'test_*.py'` → all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py tests/test_menu.py
git commit -m "feat(menu): data pickers before Docker prompts; forced produce after a switch"
```

---

### Task 7: Docs, literal check, parent YAML, compatibility

**Files:**
- Modify: `AGENTS.md:49`, `README.md` (data section ~lines 30-60), `docs/workflows/validation.md` (evidence schema ~line 28; data section ~line 65)
- Modify (parent repo): `tests/performance/k6/workflows/http-cart.yaml`, `tests/performance/k6/workflows/http-purchase.yaml`, `tests/performance/k6/README.md:123-127`, submodule pointer `vendor/punch`

- [ ] **Step 1: Literal check**

Run: `git diff main -- src/punch | grep -E '^\+.*(carts|orders|users|RUN_ID|http-|browser-)'`
Expected: empty.

- [ ] **Step 2: `AGENTS.md:49`** — replace with:

```markdown
- A dataset is written only when the run opts in with `--produce <dataset>`; interactive prompts are limited to that opt-in, the data-source picker (optional datasets) and producer picker (missing data) shown before Docker on a TTY — still one Compose run, skipped with `--no-input` — and the consumed-data delete prompt
```

- [ ] **Step 3: `README.md`** — in the YAML example under `produces` add
  `        recommended: true   # optional`; after the "A consumer is
  preflighted before Docker…" bullet add:

```markdown
- In a terminal, `punch run <workflow>` and the `punch` menu settle data
  before Docker (`punch.data_plan`). Each optional dataset with more than one
  available source offers `default (built-in)` or its data file (cursor on
  default). Then the normal preflight runs; when a dataset the run reads has
  no rows, an arrow-key list of every producer opens — the one whose product
  sets `recommended: true` labeled and preselected (first by name if
  several), producers missing required environment tagged `needs <VAR>`.
  Picking one runs it with `--produce <dataset>` — still one Compose run —
  and walks further when it is missing data too; the hint names the workflow
  to re-run and what it still misses. Esc cancels. Non-interactive
  equivalents: `--no-input`, `--data <dataset>=default`,
  `--data <dataset>=<path>`, or run the producer with `--produce`.
```

- [ ] **Step 4: `docs/workflows/validation.md`** — evidence schema per-result keys add:

```text
"switchedFrom": ["<workflow>", ...]          // only when a producer pick replaced the selected workflow
"dataSources": {"<dataset>": "default" | "<path>"}  // every dataset the workflow declares it reads
```

and add the Step 3 paragraph to the data section.

- [ ] **Step 5: Full suite** — `PY -m unittest discover -s tests -p 'test_*.py'` → all OK

- [ ] **Step 6: Commit (submodule)**

```bash
git add AGENTS.md README.md docs/workflows/validation.md
git commit -m "docs: data source and producer pickers, recommended producers"
```

- [ ] **Step 7: Parent repo YAML + README** — branch first if on `main`.
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
    `--produce`, and tells you which workflow to re-run. `--no-input` and
    `--data <dataset>=default` are the non-interactive equivalents."

- [ ] **Step 8: Verify real catalog + deprecated glue** (parent repo root)

```bash
PYTHONPATH=vendor/punch/src /tmp/punch-venv/bin/python -c \
  "from pathlib import Path; from punch.catalog import load_catalog; c = load_catalog(Path('tests/performance/k6/workflows')); print(c.recommended_producer('carts'), c.recommended_producer('orders'), c.producers_of('orders'))"
PYTHONPATH=scripts /tmp/punch-venv/bin/python -c "import pg.k6runner; print('k6runner ok')"
pnpm pg:test
```

Expected: `http-cart http-purchase ('browser-purchase', 'http-orders', 'http-purchase')`, `k6runner ok`, pg tests OK.

- [ ] **Step 9: Commit (parent)**

```bash
git add vendor/punch tests/performance/k6/workflows/http-cart.yaml \
  tests/performance/k6/workflows/http-purchase.yaml tests/performance/k6/README.md
git commit -m "feat(perf): recommend http producers; bump punch data pickers"
```

Push only when asked.

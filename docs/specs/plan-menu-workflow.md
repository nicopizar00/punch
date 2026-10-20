# Punch workflows in the menu: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `punch menu` gains a `Run workflow` option that builds a Punch workflow one step at a time, sizes every earlier step for the step after it, runs one Docker Compose run per step, and records `reports/state/punch-workflow.json`.

**Architecture:** A new standard-library module, `punch.workflow`, owns the step candidates (`first_jobs`, `next_jobs`), the planner (`plan_workflow`: data linking, then chained sizing from the last step back through `punch.sizing.size_producer`), and the runner (`run_workflow`: one `execution.execute_job` call per step, evidence after every step). `punch.menu` adds the picker flow on top. `Run job`, `punch run`, and `--size-for` keep their behavior.

**Tech Stack:** Python 3.10+ (standard library in the engine), `unittest`, PyYAML and simple-term-menu and rich (menu only, pinned in `requirements.txt`), ruff 0.7.4.

**Spec:** [`spec-menu-workflow.md`](spec-menu-workflow.md) (same directory). Read it before Task 1; this plan argues from it.

## Global Constraints

- All paths are relative to the Punch repository root (`vendor/punch` inside the parent repository). Run every command from there.
- Branch: create `feat/menu-workflow` from `docs/spec-menu-workflow` before Task 1: `git checkout -b feat/menu-workflow docs/spec-menu-workflow`.
- Host `python3` may lack `rich`. Every test command uses the pinned dependencies through `uv`:
  - one file: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
  - full suite: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_*.py'`
  - lint gates: `uvx ruff@0.7.4 format src tests && uvx ruff@0.7.4 check src tests`
- `src/punch/workflow.py` imports only the standard library and `punch.*` (CLAUDE.md rule 2: outside YAML loading and the menu, the engine is standard library only).
- No job or dataset name appears in `src/punch/*.py` (spec acceptance criterion).
- Each step is exactly one `execute_job` call, which is one `docker compose run --rm`.
- Every existing test passes unchanged, except `test_monitoring_setup_is_a_stub`, whose top-level index moves from 1 to 2 because `Run workflow` takes index 1.
- Exact user-facing strings come from the spec: `Run workflow`, `Step 1:`, `Step <n>, after <job>:`, `<job>  reads <D>[, <D2>]`, `Done (<n> steps)`, `Load options for <job> (last step):`, `Data sizing (each earlier step is sized for the next)? (Y/n)`, `Run these <n> steps? (y/N)`, `[punch] workflow not run.`, `[punch] no job reads what <job> writes; the workflow has <n> steps`, `[punch] no job in <jobs dir> writes data another job reads`.
- Commit messages follow Conventional Commits and carry no AI attribution lines.
- New documentation prose uses no em dashes, en dashes, or ` -- `.

## Review Focus

1. A producer whose k6 run exits 0 but fails afterwards (no `[DATA]` rows, invalid rows): the step is `failed`, later steps are `skipped`, and the exit code is 1, never 0. Pinned in Task 3 (`test_a_step_that_fails_after_k6_exits_0_still_exits_1`).
2. Docker never starts for step 1 (`child_exit_code` is `None`): exit code 1, no delete prompt, evidence `exitCode` is `null`. Pinned in Task 3 (`test_a_step_that_never_starts_docker_exits_1_without_a_delete_prompt`).
3. A writer whose dataset file lies outside the reader's `data.directory`: a plan error before any Docker call, because `execution.data_environment` cannot map the override. Pinned in Task 2 (`test_a_writer_outside_the_reader_data_directory_is_an_error`).
4. No options directory beside the jobs directory: the last step runs its job default without a picker, and the earlier steps are sized for that default. Pinned in Task 4 (`test_without_options_the_last_step_runs_its_job_default`).
5. A `punch-workflow.json` left by an earlier run: replaced before step 1 starts, so a crash mid-run never shows the old outcome. Pinned in Task 3 (`test_evidence_from_an_earlier_run_is_replaced_before_step_1`).

---

### Task 1: Step candidates

**Files:**
- Create: `src/punch/workflow.py`
- Create: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `punch.catalog.JobCatalog` (`.jobs`: name → `K6Job`, in sorted file-name order), `punch.job.K6Job` (`.data`: `DataSpec | None` with `.produces[].dataset/.targets`, `.requires`, `.optional`), `punch.sizing.SizingPlan`.
- Produces:
  - `AUTO = "auto"`; `Load = str | Path | None`
  - `WorkflowStep` dataclass: `number: int`, `job: K6Job`, `load: Load`, `config: dict[str, Any]`, `config_path: Path | None`, `sizing: SizingPlan | None`, `sized_for: int | None`, `produce: tuple[str, ...]`, `reads: dict[str, int]`, `built_in: tuple[str, ...]`, `readers: dict[str, list[int]]`, `rows_needed: dict[str, int]`, property `name -> str`
  - `WorkflowPlan` dataclass: `steps: list[WorkflowStep]`, `errors: list[str]`, `warnings: list[str]`
  - `first_jobs(catalog: JobCatalog) -> tuple[str, ...]`
  - `next_jobs(chosen: Sequence[str], catalog: JobCatalog) -> tuple[tuple[str, tuple[str, ...]], ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workflow.py`:

```python
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.workflow import first_jobs, next_jobs

# The parent repository's http-cart, http-orders, and http-orders-status values.
CART_SIZING = {"iterationSeconds": 1.1, "maxSeconds": 270}
ORDERS_SIZING = {"iterationSeconds": 1.1, "maxSeconds": 270, "margin": 0.15}
STATUS_SIZING = {"iterationSeconds": 0.6, "margin": 0.15}
ITERATIONS_5 = {
    "scenarios": {
        "default": {
            "executor": "shared-iterations",
            "vus": 1,
            "iterations": 5,
            "maxDuration": "5m",
        }
    }
}
CONSTANT_5_VUS_5M = {
    "scenarios": {"default": {"executor": "constant-vus", "vus": 5, "duration": "5m"}}
}


def job_yaml(
    name: str,
    *,
    produces: dict[str, list[str]] | None = None,
    requires: list[str] | None = None,
    optional: list[str] | None = None,
    sizing: dict | None = None,
    config: str | None = None,
    directory: str = "data",
    forward: list[str] | None = None,
    summary: str | None = None,
) -> str:
    lines = [
        "apiVersion: punch/v1",
        "kind: K6Job",
        "metadata:",
        f"  name: {name}",
        "spec:",
        "  workingDirectory: .",
        "  compose:",
        "    file: docker-compose.yml",
        "    service: k6",
        "  k6:",
        f"    script: /scripts/{name}.js",
    ]
    if config is not None:
        lines.append(f"    config: {config}")
    if forward:
        lines += ["  environment:", f"    forward: [{', '.join(forward)}]"]
    if summary is not None:
        lines += ["  outputs:", "    summary:", f"      path: {summary}"]
    if produces or requires or optional:
        lines += ["  data:", f"    directory: {directory}", "    mountedAt: /scripts/data"]
        if produces:
            lines.append("    produces:")
            for dataset, targets in produces.items():
                lines += [
                    f"      - dataset: {dataset}",
                    "        columns: [id]",
                    f"        targets: [{', '.join(targets)}]",
                ]
        if requires:
            lines.append(f"    requires: [{', '.join(requires)}]")
        if optional:
            lines.append(f"    optional: [{', '.join(optional)}]")
    if sizing is not None:
        lines.append("  sizing:")
        lines += [f"    {key}: {value}" for key, value in sizing.items()]
    return "\n".join(lines) + "\n"


class WorkflowCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "docker-compose.yml").touch()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, **kwargs) -> None:
        (self.root / f"{name}.yaml").write_text(job_yaml(name, **kwargs), encoding="utf-8")

    def write_options(self, name: str, config: dict) -> Path:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_chain(self) -> None:
        """cart writes carts for orders; orders writes orders for status."""
        self.write("cart", produces={"carts": ["orders"]}, sizing=CART_SIZING)
        self.write(
            "orders", requires=["carts"], produces={"orders": ["status"]}, sizing=ORDERS_SIZING
        )
        self.write("status", requires=["orders"], sizing=STATUS_SIZING)

    def catalog(self):
        return load_catalog(self.root)


class CandidateTests(WorkflowCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("audit", optional=["carts"])
        self.write("cart", produces={"carts": ["audit", "orders"]})
        self.write("lonely")
        self.write("orders", requires=["carts"], produces={"orders": ["receipt", "status"]})
        self.write("pay", produces={"payments": ["receipt"]})
        self.write("receipt", requires=["orders", "payments"])
        self.write("status", requires=["orders"])

    def test_first_jobs_need_no_earlier_step_and_have_a_next_job(self) -> None:
        # audit writes nothing, lonely has no data, pay's only reader needs orders too.
        self.assertEqual(first_jobs(self.catalog()), ("cart",))

    def test_next_jobs_follow_the_last_step_in_catalog_order(self) -> None:
        self.assertEqual(
            next_jobs(["cart"], self.catalog()),
            (("audit", ("carts",)), ("orders", ("carts",))),
        )

    def test_next_jobs_need_every_required_dataset_written(self) -> None:
        catalog = self.catalog()
        self.assertEqual(next_jobs(["cart", "orders"], catalog), (("status", ("orders",)),))
        self.assertEqual(
            next_jobs(["pay", "cart", "orders"], catalog),
            (("receipt", ("orders",)), ("status", ("orders",))),
        )

    def test_next_jobs_never_repeat_a_job(self) -> None:
        self.write("ping", optional=["pongs"], produces={"pings": ["pong"]})
        self.write("pong", optional=["pings"], produces={"pongs": ["ping"]})
        catalog = self.catalog()
        self.assertEqual(next_jobs(["ping"], catalog), (("pong", ("pings",)),))
        self.assertEqual(next_jobs(["ping", "pong"], catalog), ())

    def test_nothing_follows_an_empty_or_finished_workflow(self) -> None:
        catalog = self.catalog()
        self.assertEqual(next_jobs([], catalog), ())
        self.assertEqual(next_jobs(["cart", "orders", "status"], catalog), ())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
Expected: `ImportError` / `ModuleNotFoundError: No module named 'punch.workflow'`.

- [ ] **Step 3: Write the module**

Create `src/punch/workflow.py`:

```python
"""Plan and run a Punch workflow: Punch jobs in order, each step reading the
datasets that earlier steps write.

Standard-library only. `first_jobs` and `next_jobs` list the jobs a workflow
can start with and continue with, from the catalog's dataset links.
Spec: docs/specs/spec-menu-workflow.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from punch.catalog import JobCatalog
from punch.job import K6Job
from punch.sizing import SizingPlan

# A step's load: AUTO (sized for its reader when Punch can size the pair), a
# k6 options JSON path, or None (the job's own spec.k6.config, never sized).
AUTO = "auto"
Load = str | Path | None


@dataclass
class WorkflowStep:
    number: int
    job: K6Job
    load: Load
    # The k6 options this step runs with (a sized step's generated config).
    config: dict[str, Any] = field(default_factory=dict)
    # The file passed as `--config`; None for script options or until a sized
    # config is written.
    config_path: Path | None = None
    sizing: SizingPlan | None = None
    sized_for: int | None = None
    produce: tuple[str, ...] = ()
    # dataset -> number of the step that writes it
    reads: dict[str, int] = field(default_factory=dict)
    # optional datasets left on the scenario's built-in data
    built_in: tuple[str, ...] = ()
    # dataset -> numbers of the later steps that read it from this step
    readers: dict[str, list[int]] = field(default_factory=dict)
    # dataset -> rows its first reader reads, when estimable
    rows_needed: dict[str, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.job.name


@dataclass
class WorkflowPlan:
    steps: list[WorkflowStep]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _edge_datasets(producer: K6Job, consumer: K6Job) -> tuple[str, ...]:
    """Datasets `producer` writes for `consumer`: it names the consumer as a
    target, and the consumer requires or optionally reads them."""
    if producer.data is None or consumer.data is None:
        return ()
    reads = (*consumer.data.requires, *consumer.data.optional)
    return tuple(
        product.dataset
        for product in producer.data.produces
        if consumer.name in product.targets and product.dataset in reads
    )


def _written(chosen: Sequence[str], catalog: JobCatalog) -> set[str]:
    written: set[str] = set()
    for name in chosen:
        data = catalog.jobs[name].data
        if data is not None:
            written.update(product.dataset for product in data.produces)
    return written


def next_jobs(
    chosen: Sequence[str], catalog: JobCatalog
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Jobs that can follow `chosen`, in catalog order, each with the datasets it
    reads from the last chosen job: not chosen yet, linked from the last chosen
    job, and every dataset it requires written by a chosen job."""
    if not chosen:
        return ()
    last = catalog.jobs[chosen[-1]]
    written = _written(chosen, catalog)
    candidates: list[tuple[str, tuple[str, ...]]] = []
    for name, job in catalog.jobs.items():
        if name in chosen:
            continue
        datasets = _edge_datasets(last, job)
        if not datasets:
            continue
        if job.data is not None and not set(job.data.requires) <= written:
            continue
        candidates.append((name, datasets))
    return tuple(candidates)


def first_jobs(catalog: JobCatalog) -> tuple[str, ...]:
    """Jobs that need no earlier step and that at least one job can follow."""
    return tuple(
        name
        for name, job in catalog.jobs.items()
        if (job.data is None or not job.data.requires) and next_jobs([name], catalog)
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
Expected: 5 tests, `OK`.

- [ ] **Step 5: Format and lint**

Run: `uvx ruff@0.7.4 format src tests && uvx ruff@0.7.4 check src tests`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/punch/workflow.py tests/test_workflow.py
git commit -m "feat(workflow): list the jobs a Punch workflow can start and continue with"
```

---

### Task 2: Plan a workflow

**Files:**
- Modify: `src/punch/workflow.py` (import block; append functions)
- Modify: `tests/test_workflow.py` (import block; append `PlanTests`)

**Interfaces:**
- Consumes: Task 1's `AUTO`, `Load`, `WorkflowStep`, `WorkflowPlan`; `punch.job.read_k6_config(path) -> dict` (raises `JobError`); `punch.sizing.size_producer(producer, target_name, catalog, config, *, preset=None) -> SizingPlan` (raises `SizingError`); `punch.sizing.rows_needed(target, config) -> int` (raises `SizingError`); `JobCatalog.producers_of(dataset) -> tuple[str, ...]`; `DataSpec.host_path(dataset) -> Path`, `DataSpec.product(dataset)`, `DataSpec.directory` (resolved absolute path).
- Produces:
  - `plan_workflow(jobs: Sequence[str], loads: Sequence[Load], catalog: JobCatalog) -> WorkflowPlan`
  - `step_label(step: WorkflowStep) -> str`
  - `data_label(step: WorkflowStep) -> str`

- [ ] **Step 1: Write the failing tests**

In `tests/test_workflow.py`, replace the line `from punch.workflow import first_jobs, next_jobs` with:

```python
from punch.workflow import AUTO, data_label, first_jobs, next_jobs, plan_workflow, step_label
```

Then add this class before `if __name__ == "__main__":`:

```python
class PlanTests(WorkflowCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_chain()
        self.iterations_5 = self.write_options("5-iterations", ITERATIONS_5)
        self.constant_5m = self.write_options("5-vu-5m", CONSTANT_5_VUS_5M)

    def plan(self, loads, jobs=("cart", "orders", "status")):
        return plan_workflow(list(jobs), list(loads), self.catalog())

    def test_each_earlier_step_is_sized_for_the_next(self) -> None:
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.warnings, [])
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            [
                "auto: sized for step 2 (7 iterations, 1 VU)",
                "auto: sized for step 3 (6 iterations, 1 VU)",
                "5-iterations",
            ],
        )
        cart, orders, _status = plan.steps
        # status reads 5 rows; orders runs ceil(5 x 1.15) = 6; cart ceil(6 x 1.15) = 7.
        self.assertEqual((orders.sizing.rows_needed, cart.sizing.rows_needed), (5, 6))
        self.assertEqual(cart.config, {"vus": 1, "iterations": 7})
        self.assertEqual((cart.rows_needed, orders.rows_needed), ({"carts": 6}, {"orders": 5}))

    def test_a_duration_load_sizes_both_producers(self) -> None:
        plan = self.plan([AUTO, AUTO, self.constant_5m])
        self.assertEqual(plan.errors, [])
        # status reads ceil(5 x 300 / 0.6) = 2500 rows.
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            [
                "auto: sized for step 2 (3307 iterations, 14 VUs)",
                "auto: sized for step 3 (2875 iterations, 12 VUs)",
                "5-vu-5m",
            ],
        )
        self.assertEqual(plan.steps[1].rows_needed, {"orders": 2500})

    def test_each_read_links_to_its_writer_and_writers_produce_only_what_is_read(self) -> None:
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual([step.reads for step in plan.steps], [{}, {"carts": 1}, {"orders": 2}])
        self.assertEqual([step.produce for step in plan.steps], [("carts",), ("orders",), ()])
        self.assertEqual(
            [data_label(step) for step in plan.steps],
            [
                "writes carts",
                "reads carts from step 1; writes orders",
                "reads orders from step 2",
            ],
        )

    def test_a_dataset_no_later_step_reads_is_not_produced(self) -> None:
        plan = self.plan([AUTO, self.iterations_5], jobs=("cart", "orders"))
        self.assertEqual([step.produce for step in plan.steps], [("carts",), ()])

    def test_without_sizing_every_step_runs_the_given_file(self) -> None:
        plan = self.plan([self.iterations_5] * 3)
        self.assertEqual(plan.errors, [])
        self.assertEqual([step.sizing for step in plan.steps], [None, None, None])
        self.assertEqual([step.config_path for step in plan.steps], [self.iterations_5] * 3)
        self.assertEqual([step_label(step) for step in plan.steps], ["5-iterations"] * 3)

    def test_a_job_default_load_is_never_sized(self) -> None:
        self.write_options("own", ITERATIONS_5)
        self.write("cart", produces={"carts": ["orders"]}, sizing=CART_SIZING, config="own.json")
        plan = self.plan([None, None, None])
        self.assertEqual(plan.errors, [])
        self.assertEqual([step.sizing for step in plan.steps], [None, None, None])
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            ["job default (own)", "script options", "script options"],
        )

    def test_an_auto_step_without_a_reader_runs_its_own_config(self) -> None:
        plan = self.plan([AUTO, AUTO, AUTO])
        # status's script options run 1 iteration: orders ceil(1 x 1.15) = 2, cart 3.
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            [
                "auto: sized for step 2 (3 iterations, 1 VU)",
                "auto: sized for step 3 (2 iterations, 1 VU)",
                "script options",
            ],
        )

    def test_a_pair_punch_cannot_size_warns_and_runs_the_job_config(self) -> None:
        self.write("orders", requires=["carts"], produces={"orders": ["status"]})
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(plan.errors, [])
        self.assertEqual(
            plan.warnings,
            [
                "step 2 orders: not sized for step 3 (orders is not a sizable producer: needs "
                "sizing.iterationSeconds and sizing.maxSeconds); it runs its default config",
                "step 1 cart: not sized for step 2 (orders declares no spec.sizing); "
                "it runs its default config",
            ],
        )
        self.assertEqual(
            [step_label(step) for step in plan.steps[:2]], ["script options", "script options"]
        )

    def test_two_readers_of_one_writer_reuse_its_rows(self) -> None:
        self.write("cart", produces={"carts": ["orders", "audit"]}, sizing=CART_SIZING)
        self.write("audit", requires=["carts"])
        plan = self.plan([AUTO, AUTO, self.iterations_5], jobs=("cart", "orders", "audit"))
        self.assertEqual(plan.errors, [])
        self.assertIn("steps 2 and 3 read carts from step 1; its rows are reused", plan.warnings)

    def test_an_optional_dataset_without_an_earlier_writer_uses_built_in_data(self) -> None:
        self.write("audit", optional=["carts"], produces={"audits": ["report"]})
        self.write("report", requires=["audits"])
        plan = self.plan([AUTO, self.iterations_5], jobs=("audit", "report"))
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.steps[0].built_in, ("carts",))
        self.assertEqual(data_label(plan.steps[0]), "carts: built-in data; writes audits")

    def test_a_writer_outside_the_reader_data_directory_is_an_error(self) -> None:
        self.write("status", requires=["orders"], sizing=STATUS_SIZING, directory="other")
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(
            plan.errors,
            [
                f"step 3 status reads orders from step 2, but {self.root / 'data' / 'orders.csv'} "
                f"is outside its data directory {self.root / 'other'}"
            ],
        )

    def test_an_unreadable_options_file_is_an_error(self) -> None:
        missing = self.root / "missing.json"
        plan = self.plan([AUTO, AUTO, missing])
        [error] = plan.errors
        self.assertTrue(error.startswith(f"step 3 status: could not read k6 config {missing}"))

    def test_bad_requests_are_errors_not_exceptions(self) -> None:
        catalog = self.catalog()
        cases = {
            "no jobs": (
                [],
                [],
                "a workflow needs at least one job and one load per job; got 0 jobs and 0 loads",
            ),
            "lengths differ": (
                ["cart"],
                [AUTO, AUTO],
                "a workflow needs at least one job and one load per job; got 1 job and 2 loads",
            ),
            "unknown job": (["cart", "nope"], [AUTO, AUTO], 'step 2: unknown job "nope"'),
            "repeated job": (
                ["cart", "orders", "cart"],
                [AUTO, AUTO, AUTO],
                "step 3 cart repeats step 1",
            ),
            "unknown load": (["cart"], ["5-iterations"], "step 1 cart: unknown load '5-iterations'"),
            "missing writer": (
                ["orders", "status"],
                [AUTO, AUTO],
                "step 1 orders reads carts; no earlier step writes it. Put cart before step 1",
            ),
        }
        for case, (jobs, loads, error) in cases.items():
            with self.subTest(case=case):
                plan = plan_workflow(jobs, loads, catalog)
                self.assertEqual(plan.errors, [error])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
Expected: `ImportError: cannot import name 'data_label' from 'punch.workflow'`.

- [ ] **Step 3: Write the planner**

In `src/punch/workflow.py`, replace the module docstring and import block (everything above `# A step's load`) with:

```python
"""Plan and run a Punch workflow: Punch jobs in order, each step reading the
datasets that earlier steps write.

Standard-library only. `first_jobs` and `next_jobs` list the jobs a workflow
can start with and continue with, from the catalog's dataset links.
`plan_workflow` links every read to the latest earlier writer, then resolves
each step's k6 options from the last step back: an AUTO step that writes data
for a later step is sized for that reader with `punch.sizing.size_producer`,
so a sized reader passes its own sized config to the step before it.
Spec: docs/specs/spec-menu-workflow.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from punch.catalog import JobCatalog
from punch.job import JobError, K6Job, read_k6_config
from punch.sizing import SizingError, SizingPlan, rows_needed, size_producer
```

Then append to the end of `src/punch/workflow.py`:

```python
def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def plan_workflow(
    jobs: Sequence[str], loads: Sequence[Load], catalog: JobCatalog
) -> WorkflowPlan:
    """Validate, link, and size a workflow without running it. A bad request
    becomes an entry in `errors`, never an exception."""
    plan = WorkflowPlan(steps=[])
    _select_steps(jobs, loads, catalog, plan)
    if not plan.errors:
        _link_data(catalog, plan)
    if not plan.errors:
        _resolve_configs(catalog, plan)
    return plan


def _select_steps(
    jobs: Sequence[str], loads: Sequence[Load], catalog: JobCatalog, plan: WorkflowPlan
) -> None:
    """Check names and loads; read every options file; fill plan.steps."""
    if not jobs or len(jobs) != len(loads):
        plan.errors.append(
            "a workflow needs at least one job and one load per job; "
            f"got {_count(len(jobs), 'job')} and {_count(len(loads), 'load')}"
        )
        return
    seen: dict[str, int] = {}
    for number, (name, load) in enumerate(zip(jobs, loads), start=1):
        job = catalog.jobs.get(name)
        if job is None:
            plan.errors.append(f'step {number}: unknown job "{name}"')
            continue
        if name in seen:
            plan.errors.append(f"step {number} {name} repeats step {seen[name]}")
            continue
        seen[name] = number
        step = WorkflowStep(number, job, load)
        if isinstance(load, Path):
            try:
                step.config = read_k6_config(load)
            except JobError as error:
                plan.errors.append(f"step {number} {name}: {error}")
                continue
            step.config_path = load
        elif load != AUTO and load is not None:
            plan.errors.append(f"step {number} {name}: unknown load {load!r}")
            continue
        plan.steps.append(step)


def _link_data(catalog: JobCatalog, plan: WorkflowPlan) -> None:
    """Point each read at the latest earlier writer; writers produce only what is read."""
    writers: dict[str, WorkflowStep] = {}
    for step in plan.steps:
        data = step.job.data
        if data is None:
            continue
        built_in: list[str] = []
        for dataset in (*data.requires, *data.optional):
            writer = writers.get(dataset)
            if writer is None:
                if dataset in data.requires:
                    producers = " or ".join(catalog.producers_of(dataset)) or "a producer"
                    plan.errors.append(
                        f"step {step.number} {step.name} reads {dataset}; no earlier step "
                        f"writes it. Put {producers} before step {step.number}"
                    )
                else:
                    built_in.append(dataset)
                continue
            assert writer.job.data is not None  # a writer produces the dataset
            source = writer.job.data.host_path(dataset)
            if data.directory not in source.parents:
                plan.errors.append(
                    f"step {step.number} {step.name} reads {dataset} from step {writer.number}, "
                    f"but {source} is outside its data directory {data.directory}"
                )
                continue
            step.reads[dataset] = writer.number
            writer.readers.setdefault(dataset, []).append(step.number)
        step.built_in = tuple(built_in)
        for product in data.produces:
            writers[product.dataset] = step
    for step in plan.steps:
        data = step.job.data
        if data is None:
            continue
        step.produce = tuple(p.dataset for p in data.produces if p.dataset in step.readers)
        for dataset, readers in step.readers.items():
            if len(readers) > 1:
                plan.warnings.append(
                    f"steps {_and([str(number) for number in readers])} read {dataset} "
                    f"from step {step.number}; its rows are reused"
                )


def _sizing_reader(
    step: WorkflowStep, by_number: dict[int, WorkflowStep]
) -> WorkflowStep | None:
    """The first later step that reads this step's data and is one of its targets."""
    data = step.job.data
    candidates: list[WorkflowStep] = []
    for dataset, readers in step.readers.items():
        product = data.product(dataset) if data is not None else None
        targets = product.targets if product is not None else ()
        candidates += [by_number[n] for n in readers if by_number[n].name in targets]
    return min(candidates, key=lambda reader: reader.number, default=None)


def _resolve_configs(catalog: JobCatalog, plan: WorkflowPlan) -> None:
    """Resolve each step's k6 options from the last step back, sizing AUTO writers."""
    by_number = {step.number: step for step in plan.steps}
    for step in reversed(plan.steps):
        if isinstance(step.load, Path):
            continue  # read when the step was selected
        reader = _sizing_reader(step, by_number) if step.load == AUTO else None
        if reader is not None:
            preset = reader.config_path.stem if reader.config_path is not None else None
            try:
                sizing = size_producer(step.job, reader.name, catalog, reader.config, preset=preset)
            except SizingError as error:
                plan.warnings.append(
                    f"step {step.number} {step.name}: not sized for step {reader.number} "
                    f"({error}); it runs its default config"
                )
            else:
                step.sizing, step.sized_for = sizing, reader.number
                step.config = dict(sizing.config)
                continue
        step.config_path = step.job.k6_config
        step.config = read_k6_config(step.config_path) if step.config_path is not None else {}
    for step in plan.steps:
        for dataset, readers in step.readers.items():
            reader = by_number[readers[0]]
            try:
                step.rows_needed[dataset] = rows_needed(reader.job, reader.config)
            except SizingError:
                pass


def step_label(step: WorkflowStep) -> str:
    """The plan's load text for one step."""
    if step.sizing is not None:
        return (
            f"auto: sized for step {step.sized_for} "
            f"({_count(step.sizing.iterations, 'iteration')}, {_count(step.sizing.vus, 'VU')})"
        )
    if isinstance(step.load, Path):
        return step.load.stem
    if step.config_path is not None:
        return f"job default ({step.config_path.stem})"
    return "script options"


def data_label(step: WorkflowStep) -> str:
    """What the step reads, leaves on built-in data, and writes."""
    parts = [f"reads {dataset} from step {writer}" for dataset, writer in step.reads.items()]
    parts += [f"{dataset}: built-in data" for dataset in step.built_in]
    parts += [f"writes {dataset}" for dataset in step.produce]
    return "; ".join(parts) or "none"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
Expected: 18 tests, `OK`.

- [ ] **Step 5: Format and lint**

Run: `uvx ruff@0.7.4 format src tests && uvx ruff@0.7.4 check src tests`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/punch/workflow.py tests/test_workflow.py
git commit -m "feat(workflow): plan a Punch workflow and size each step for the next"
```

---

### Task 3: Run a workflow and record its evidence

**Files:**
- Modify: `src/punch/execution.py` (add `import json`; add `print_metrics` after `confirm_delete_consumed`)
- Modify: `src/punch/menu.py` (import `print_metrics`; delete `_print_metrics`; call `print_metrics`)
- Modify: `src/punch/workflow.py` (import block; append the runner)
- Modify: `tests/test_workflow.py` (import block; append `RunTests` and `_Terminal`)

**Interfaces:**
- Consumes: Task 2's `plan_workflow`, `step_label`, `WorkflowPlan`, `WorkflowStep`; `punch.execution.execute_job(job, *, environment, produce, data_overrides, optional_choices, producers_of, docker_run_confirmed, config) -> ExecutionResult`; `ExecutionResult(job_name, command, child_exit_code, passed, failure, datasets)`; `DatasetResult(dataset, path, record_count, published)`; `confirm_delete_consumed(paths, *, stdin, stdout)`; `punch.sizing.write_producer_config(plan, directory) -> Path` (writes `k6-config-<producer>.json`); `punch.sizing.sizing_evidence(plan, produced) -> dict`.
- Produces:
  - `punch.execution.print_metrics(job: K6Job, stdout: IO[str] | None = None) -> None`
  - `EVIDENCE_NAME = "punch-workflow.json"`
  - `run_workflow(plan: WorkflowPlan, catalog: JobCatalog, *, environment: Mapping[str, str], base_urls: Mapping[str, str], state_dir: Path, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

In `tests/test_workflow.py`, add `import io` above `import json` and `from unittest.mock import patch` below `from tempfile import TemporaryDirectory`. Then replace the two `punch` import lines below `sys.path.insert(...)` with:

```python
from punch.catalog import load_catalog
from punch.execution import DatasetResult, ExecutionResult
from punch.workflow import (
    AUTO,
    data_label,
    first_jobs,
    next_jobs,
    plan_workflow,
    run_workflow,
    step_label,
)
```

Then add these classes before `if __name__ == "__main__":`:

```python
class RunTests(WorkflowCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_chain()
        self.iterations_5 = self.write_options("5-iterations", ITERATIONS_5)
        self.state_dir = self.root / "state"
        self.calls: list[tuple[str, dict]] = []
        # job name -> (k6 exit code or None, passed, rows written) or "interrupt"
        self.outcomes: dict = {}

    def passing(self) -> dict:
        return {
            "cart": (0, True, {"carts": 7}),
            "orders": (0, True, {"orders": 6}),
            "status": (0, True, {}),
        }

    def fake_execute(self, job, **kwargs) -> ExecutionResult:
        self.calls.append((job.name, kwargs))
        outcome = self.outcomes.get(job.name, (0, True, {}))
        if outcome == "interrupt":
            raise KeyboardInterrupt
        code, passed, rows = outcome
        datasets = tuple(
            DatasetResult(dataset, self.root / "data" / f"{dataset}.csv", count, passed)
            for dataset, count in rows.items()
        )
        failure = None if passed else "k6 failed"
        return ExecutionResult(job.name, ("docker",), code, passed, failure, datasets)

    def run_plan(self, loads, jobs=("cart", "orders", "status"), stdin=None):
        plan = plan_workflow(list(jobs), list(loads), self.catalog())
        self.assertEqual(plan.errors, [])
        output = io.StringIO()
        with patch("punch.workflow.execute_job", side_effect=self.fake_execute):
            code = run_workflow(
                plan,
                self.catalog(),
                environment={"PATH": "/bin"},
                base_urls={"k6": "http://app"},
                state_dir=self.state_dir,
                stdin=stdin if stdin is not None else io.StringIO(),
                stdout=output,
            )
        return code, output.getvalue(), self.evidence()

    def evidence(self) -> dict:
        return json.loads((self.state_dir / "punch-workflow.json").read_text(encoding="utf-8"))

    def test_runs_every_step_in_order_with_its_data_wiring(self) -> None:
        self.outcomes = self.passing()
        code, text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(code, 0)
        self.assertEqual([name for name, _ in self.calls], ["cart", "orders", "status"])
        cart, orders, status = (kwargs for _, kwargs in self.calls)
        data = self.root / "data"
        self.assertEqual(
            (cart["produce"], cart["data_overrides"], cart["optional_choices"]),
            (("carts",), {}, {}),
        )
        self.assertEqual(orders["data_overrides"], {"carts": data / "carts.csv"})
        self.assertEqual(orders["produce"], ("orders",))
        self.assertEqual(status["data_overrides"], {"orders": data / "orders.csv"})
        self.assertEqual(status["produce"], ())
        self.assertEqual(cart["config"], self.state_dir / "k6-config-cart.json")
        self.assertEqual(orders["config"], self.state_dir / "k6-config-orders.json")
        self.assertEqual(status["config"], self.iterations_5)
        self.assertTrue(all(kwargs["docker_run_confirmed"] for _, kwargs in self.calls))
        self.assertEqual(
            json.loads((self.state_dir / "k6-config-cart.json").read_text(encoding="utf-8")),
            {"vus": 1, "iterations": 7},
        )
        self.assertEqual(evidence["version"], 1)
        self.assertEqual((evidence["outcome"], evidence["passed"]), ("passed", True))
        self.assertEqual(evidence["jobs"], ["cart", "orders", "status"])
        self.assertEqual([step["status"] for step in evidence["steps"]], ["passed"] * 3)
        self.assertEqual(
            [step["rows"] for step in evidence["steps"]], [{"carts": 7}, {"orders": 6}, {}]
        )
        self.assertEqual(evidence["steps"][0]["sizing"]["producerIterations"], 7)
        self.assertEqual(evidence["steps"][0]["sizing"]["producedRows"], {"carts": 7})
        self.assertIsNone(evidence["steps"][2]["sizing"])
        self.assertEqual(evidence["steps"][1]["reads"], {"carts": 1})
        self.assertEqual(evidence["steps"][0]["label"], "auto: sized for step 2 (7 iterations, 1 VU)")
        self.assertEqual(evidence["warnings"], [])
        self.assertIn("[punch] evidence written: ", text)

    def test_a_failed_step_stops_the_workflow_with_its_k6_exit_code(self) -> None:
        self.outcomes = {**self.passing(), "orders": (99, False, {})}
        code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(code, 99)
        self.assertEqual([name for name, _ in self.calls], ["cart", "orders"])
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["passed", "failed", "skipped"]
        )
        self.assertEqual(
            (evidence["steps"][1]["exitCode"], evidence["steps"][1]["failure"]), (99, "k6 failed")
        )
        self.assertEqual((evidence["outcome"], evidence["passed"]), ("failed", False))

    def test_a_step_that_fails_after_k6_exits_0_still_exits_1(self) -> None:
        self.outcomes = {"cart": (0, False, {})}
        code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(code, 1)
        self.assertEqual(evidence["outcome"], "failed")

    def test_a_step_that_never_starts_docker_exits_1_without_a_delete_prompt(self) -> None:
        self.outcomes = {"cart": (None, False, {})}
        (self.root / "data").mkdir()
        (self.root / "data" / "carts.csv").write_text("id\n1\n", encoding="utf-8")
        code, text, evidence = self.run_plan(
            [AUTO, AUTO, self.iterations_5], stdin=_Terminal("y\n")
        )
        self.assertEqual(code, 1)
        self.assertEqual([name for name, _ in self.calls], ["cart"])
        self.assertNotIn("Delete consumed", text)
        self.assertTrue((self.root / "data" / "carts.csv").exists())
        self.assertIsNone(evidence["steps"][0]["exitCode"])
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["failed", "skipped", "skipped"]
        )

    def test_an_interrupt_marks_the_step_and_skips_the_rest(self) -> None:
        self.outcomes = {**self.passing(), "orders": "interrupt"}
        plan = plan_workflow(
            ["cart", "orders", "status"], [AUTO, AUTO, self.iterations_5], self.catalog()
        )
        with patch("punch.workflow.execute_job", side_effect=self.fake_execute):
            with self.assertRaises(KeyboardInterrupt):
                run_workflow(
                    plan,
                    self.catalog(),
                    environment={},
                    base_urls={},
                    state_dir=self.state_dir,
                    stdin=io.StringIO(),
                    stdout=io.StringIO(),
                )
        evidence = self.evidence()
        self.assertEqual((evidence["outcome"], evidence["passed"]), ("interrupted", False))
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["passed", "interrupted", "skipped"]
        )

    def test_a_writer_short_of_rows_warns_and_the_workflow_continues(self) -> None:
        self.outcomes = {**self.passing(), "cart": (0, True, {"carts": 4})}
        code, text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        warning = "step 1 cart wrote 4 carts rows; step 2 reads 6, so rows repeat"
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 3)
        self.assertIn(f"[punch] warning: {warning}\n", text)
        self.assertEqual(evidence["warnings"], [warning])

    def test_optional_reads_use_the_writer_file_and_unwritten_ones_built_in_data(self) -> None:
        self.write("cart", produces={"carts": ["audit", "orders"]}, sizing=CART_SIZING)
        self.write("audit", optional=["carts"])
        self.outcomes = {"cart": (0, True, {"carts": 5})}
        self.run_plan([self.iterations_5, self.iterations_5], jobs=("cart", "audit"))
        audit = dict(self.calls)["audit"]
        self.assertEqual(audit["optional_choices"], {"carts": True})
        self.assertEqual(audit["data_overrides"], {"carts": self.root / "data" / "carts.csv"})
        self.calls.clear()
        self.run_plan([self.iterations_5], jobs=("audit",))
        self.assertEqual(dict(self.calls)["audit"]["optional_choices"], {"carts": False})

    def test_base_url_reaches_only_the_jobs_that_forward_it(self) -> None:
        self.write("status", requires=["orders"], sizing=STATUS_SIZING, forward=["BASE_URL"])
        self.outcomes = self.passing()
        self.run_plan([AUTO, AUTO, self.iterations_5])
        environments = {name: kwargs["environment"] for name, kwargs in self.calls}
        self.assertEqual(environments["cart"], {"PATH": "/bin"})
        self.assertEqual(environments["status"], {"PATH": "/bin", "BASE_URL": "http://app"})

    def test_a_passing_step_prints_its_metrics(self) -> None:
        self.write("status", requires=["orders"], sizing=STATUS_SIZING, summary="status.json")
        (self.root / "status.json").write_text(
            json.dumps(
                {
                    "totalRequests": 5,
                    "errorRate": 0,
                    "p90Ms": 12.5,
                    "checkPassRate": 1,
                    "durationMs": 1500,
                }
            ),
            encoding="utf-8",
        )
        self.outcomes = self.passing()
        _code, text, _evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertIn("[punch] status metrics:\n  requests    : 5\n", text)
        self.assertIn("  duration    : 1.5s\n", text)

    def test_the_written_datasets_are_offered_for_deletion_once(self) -> None:
        self.outcomes = self.passing()
        data = self.root / "data"
        data.mkdir()
        for dataset in ("carts", "orders"):
            (data / f"{dataset}.csv").write_text("id\n1\n", encoding="utf-8")
        code, text, _evidence = self.run_plan(
            [AUTO, AUTO, self.iterations_5], stdin=_Terminal("n\nn\n")
        )
        self.assertEqual(code, 0)
        self.assertEqual(text.count('Delete consumed "carts" data'), 1)
        self.assertEqual(text.count('Delete consumed "orders" data'), 1)
        self.assertTrue((data / "carts.csv").exists())

    def test_evidence_from_an_earlier_run_is_replaced_before_step_1(self) -> None:
        self.state_dir.mkdir()
        (self.state_dir / "punch-workflow.json").write_text(
            '{"outcome": "passed", "stale": true}', encoding="utf-8"
        )
        self.outcomes = self.passing()
        seen: list[dict] = []
        original = self.fake_execute

        def watch(job, **kwargs) -> ExecutionResult:
            if job.name == "cart":
                seen.append(self.evidence())
            return original(job, **kwargs)

        self.fake_execute = watch
        self.run_plan([AUTO, AUTO, self.iterations_5])
        [at_start] = seen
        self.assertEqual(at_start["outcome"], "running")
        self.assertNotIn("stale", at_start)
        self.assertEqual({step["status"] for step in at_start["steps"]}, {"pending"})

    def test_evidence_paths_are_relative_beneath_the_current_directory(self) -> None:
        self.outcomes = self.passing()
        with patch.object(Path, "cwd", return_value=self.root):
            _code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(evidence["steps"][0]["config"], "state/k6-config-cart.json")
        self.assertEqual(evidence["steps"][0]["load"], "auto")
        self.assertEqual(evidence["steps"][2]["load"], "5-iterations.json")
        self.calls.clear()
        _code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(
            evidence["steps"][0]["config"], str(self.state_dir / "k6-config-cart.json")
        )


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
Expected: `ImportError: cannot import name 'run_workflow' from 'punch.workflow'`.

- [ ] **Step 3: Move the metrics printer into `punch.execution`**

In `src/punch/execution.py`, add `import json` to the standard-library imports (after `import csv`). Then insert this function directly above `def _data_record(`:

```python
def print_metrics(job: K6Job, stdout: IO[str] | None = None) -> None:
    """The headline numbers of the job's k6 summary, when it wrote one."""
    if job.summary_output is None or not job.summary_output.path.exists():
        return
    try:
        summary = json.loads(job.summary_output.path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    output = stdout if stdout is not None else sys.stdout
    output.write(f"[punch] {job.name} metrics:\n")
    output.write(f"  requests    : {summary.get('totalRequests', '?')}\n")
    output.write(f"  error rate  : {summary.get('errorRate', 0) * 100:.2f}%\n")
    output.write(f"  p90 duration: {summary.get('p90Ms', 0):.1f} ms\n")
    output.write(f"  check pass  : {summary.get('checkPassRate', 0) * 100:.2f}%\n")
    output.write(f"  duration    : {summary.get('durationMs', 0) / 1000:.1f}s\n")
```

In `src/punch/menu.py`:
- add `print_metrics,` to the `from punch.execution import (...)` list, after `execute_job,`;
- delete the whole `def _print_metrics(job: K6Job) -> None:` function;
- in `_run_job_menu`, replace `_print_metrics(job)` with `print_metrics(job)`.

- [ ] **Step 4: Write the runner**

In `src/punch/workflow.py`, replace the import block (from `from __future__ import annotations` down to the `punch.sizing` import) with:

```python
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Mapping, Sequence

from punch.catalog import JobCatalog
from punch.execution import (
    ExecutionResult,
    confirm_delete_consumed,
    execute_job,
    print_metrics,
)
from punch.job import JobError, K6Job, read_k6_config
from punch.sizing import (
    SizingError,
    SizingPlan,
    rows_needed,
    size_producer,
    sizing_evidence,
    write_producer_config,
)
```

Add this paragraph to the module docstring, before the `Spec:` line:

```text
`run_workflow` runs the plan, one `execute_job` call (one Docker Compose run)
per step, stops at the first failure, and records
<state_dir>/punch-workflow.json before step 1, after every step, and on
interruption.
```

Then append to the end of `src/punch/workflow.py`:

```python
EVIDENCE_NAME = "punch-workflow.json"


def run_workflow(
    plan: WorkflowPlan,
    catalog: JobCatalog,
    *,
    environment: Mapping[str, str],
    base_urls: Mapping[str, str],
    state_dir: Path,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    """Run every step in order, one Docker Compose run each; stop at the first
    failure. Returns 0, or the failed step's k6 exit code (1 when k6 exited 0 or
    never ran). A KeyboardInterrupt marks the running step, records the
    evidence, and propagates."""
    output = stdout if stdout is not None else sys.stdout
    terminal = stdin if stdin is not None else sys.stdin
    for step in plan.steps:
        if step.sizing is not None:
            step.config_path = write_producer_config(step.sizing, state_dir)
    evidence = _evidence(plan)
    path = state_dir / EVIDENCE_NAME
    _write_evidence(path, evidence)
    by_number = {step.number: step for step in plan.steps}
    exit_code = 0
    started_docker = False
    for step, record in zip(plan.steps, evidence["steps"]):
        if exit_code != 0:
            record["status"] = "skipped"
            continue
        output.write(f"[punch] workflow step {step.number}/{len(plan.steps)}: {step.name}\n")
        output.flush()
        try:
            result = execute_job(
                step.job,
                environment=_step_environment(step, environment, base_urls),
                produce=step.produce,
                data_overrides=_data_overrides(step, by_number),
                optional_choices=_optional_choices(step),
                producers_of=catalog.producers_of,
                docker_run_confirmed=True,
                config=step.config_path,
            )
        except KeyboardInterrupt:
            record["status"] = "interrupted"
            for later in evidence["steps"][step.number :]:
                later["status"] = "skipped"
            evidence["outcome"] = "interrupted"
            _write_evidence(path, evidence)
            output.write(f"[punch] evidence written: {_display(path)}\n")
            raise
        started_docker = started_docker or result.child_exit_code is not None
        _record_result(record, step, result)
        if result.passed:
            print_metrics(step.job, output)
            for warning in _shortfall(step, record["rows"]):
                evidence["warnings"].append(warning)
                output.write(f"[punch] warning: {warning}\n")
        else:
            exit_code = result.child_exit_code or 1
        _write_evidence(path, evidence)
    evidence["outcome"] = "passed" if exit_code == 0 else "failed"
    evidence["passed"] = exit_code == 0
    _write_evidence(path, evidence)
    output.write(f"[punch] evidence written: {_display(path)}\n")
    if started_docker:
        confirm_delete_consumed(_written_paths(plan), stdin=terminal, stdout=output)
    return exit_code


def _display(path: Path) -> str:
    """`path` relative to the current directory when beneath it, else absolute."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _load_text(load: Load) -> str:
    if isinstance(load, Path):
        return _display(load)
    return "job default" if load is None else "auto"


def _evidence(plan: WorkflowPlan) -> dict[str, Any]:
    return {
        "version": 1,
        "outcome": "running",
        "passed": False,
        "jobs": [step.name for step in plan.steps],
        "steps": [
            {
                "step": step.number,
                "job": step.name,
                "load": _load_text(step.load),
                "label": step_label(step),
                "config": _display(step.config_path) if step.config_path is not None else None,
                "sizing": sizing_evidence(step.sizing, {}) if step.sizing is not None else None,
                "produce": list(step.produce),
                "reads": dict(step.reads),
                "builtIn": list(step.built_in),
                "status": "pending",
                "exitCode": None,
                "failure": None,
                "rows": {},
            }
            for step in plan.steps
        ],
        "warnings": list(plan.warnings),
    }


def _write_evidence(path: Path, evidence: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def _record_result(record: dict[str, Any], step: WorkflowStep, result: ExecutionResult) -> None:
    rows = {dataset.dataset: dataset.record_count for dataset in result.datasets}
    record.update(
        status="passed" if result.passed else "failed",
        exitCode=result.child_exit_code,
        failure=result.failure,
        rows=rows,
    )
    if step.sizing is not None:
        record["sizing"] = sizing_evidence(step.sizing, rows)


def _step_environment(
    step: WorkflowStep, environment: Mapping[str, str], base_urls: Mapping[str, str]
) -> dict[str, str]:
    merged = dict(environment)
    url = base_urls.get(step.job.compose_service)
    if url and "BASE_URL" in step.job.forward_environment:
        merged["BASE_URL"] = url
    return merged


def _data_overrides(step: WorkflowStep, by_number: dict[int, WorkflowStep]) -> dict[str, Path]:
    """Each read pointed at the file its writer wrote."""
    overrides: dict[str, Path] = {}
    for dataset, writer in step.reads.items():
        data = by_number[writer].job.data
        assert data is not None  # a writer produces the dataset
        overrides[dataset] = data.host_path(dataset)
    return overrides


def _optional_choices(step: WorkflowStep) -> dict[str, bool]:
    """True for an optional dataset an earlier step wrote; False for built-in data."""
    data = step.job.data
    if data is None:
        return {}
    choices = {dataset: True for dataset in step.reads if dataset in data.optional}
    choices.update((dataset, False) for dataset in step.built_in)
    return choices


def _shortfall(step: WorkflowStep, rows: Mapping[str, int]) -> list[str]:
    return [
        f"step {step.number} {step.name} wrote {rows.get(dataset, 0)} {dataset} rows; "
        f"step {step.readers[dataset][0]} reads {needed}, so rows repeat"
        for dataset, needed in step.rows_needed.items()
        if rows.get(dataset, 0) < needed
    ]


def _written_paths(plan: WorkflowPlan) -> dict[str, Path]:
    """Every dataset file this workflow writes, for the delete prompt."""
    paths: dict[str, Path] = {}
    for step in plan.steps:
        data = step.job.data
        if data is None:
            continue
        for dataset in step.produce:
            paths[dataset] = data.host_path(dataset)
    return paths
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v`
Expected: 30 tests, `OK`.

Run the full suite too, since `menu.py` changed:
`PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_*.py'`
Expected: `OK` (357 tests: the 327 existing plus 30 new).

- [ ] **Step 6: Format and lint**

Run: `uvx ruff@0.7.4 format src tests && uvx ruff@0.7.4 check src tests`
Expected: `All checks passed!` If `ruff check` reports `I001` (unsorted imports) in `tests/test_workflow.py`, run `uvx ruff@0.7.4 check --fix src tests` and re-run the gates.

- [ ] **Step 7: Commit**

```bash
git add src/punch/execution.py src/punch/menu.py src/punch/workflow.py tests/test_workflow.py
git commit -m "feat(workflow): run a Punch workflow step by step and record punch-workflow.json"
```

---

### Task 4: `Run workflow` in the menu

**Files:**
- Modify: `src/punch/menu.py`
- Modify: `tests/test_menu.py`

**Interfaces:**
- Consumes: Tasks 1-3: `AUTO`, `Load`, `WorkflowPlan`, `first_jobs`, `next_jobs`, `plan_workflow`, `run_workflow`, `step_label`, `data_label`; the menu's existing `_select(entries, title, cursor_index=0) -> int` (raises `_MenuCancelled` on Esc), `_prompt(question, default) -> str`, `_job_menu_rows(paths)`, `_choose_options(job, options_dir) -> Path | None`, `_default_base_url(compose_service) -> str | None`.
- Produces: `run_menu` top level `["Run job", "Run workflow", "Monitoring setup"]`; `_job_menu_rows(paths, heading="Available k6 jobs")`; `_choose_options(job, options_dir, title="Load an options preset:")`; `_run_workflow_menu(jobs_dir, options_dir, state_dir) -> int`.

- [ ] **Step 1: Teach the fake Docker one output per script**

In `tests/test_menu.py`, replace the `FAKE_DOCKER` string with:

```python
FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = Path(os.environ["FAKE_DOCKER_ARGS"])
with arguments.open("a", encoding="utf-8") as file:
    file.write(json.dumps(sys.argv[1:]) + "\\n")

by_script = json.loads(os.environ.get("FAKE_DOCKER_STDOUT_BY_SCRIPT", "{}"))
script = next((argument for argument in sys.argv[1:] if argument in by_script), None)
stdout = by_script[script] if script else os.environ.get("FAKE_DOCKER_STDOUT", "")
for line in stdout.split("|"):
    if line:
        print(line, flush=True)
raise SystemExit(int(os.environ.get("FAKE_DOCKER_EXIT_CODE", "0")))
"""
```

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_menu.py'`
Expected: `OK` (the existing tests do not set the new variable).

- [ ] **Step 2: Write the failing tests**

In `tests/test_menu.py`, change `test_monitoring_setup_is_a_stub` to select index 2:

```python
    def test_monitoring_setup_is_a_stub(self) -> None:
        self.write_job("fixture")
        with self.select_menu(2):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])
```

Then add these methods inside `class MenuTests`, after `test_target_picker_lists_each_sized_target` and before `class _SelectedMenu`:

```python
    def write_chain(self, *, options: bool = True) -> None:
        """cart writes carts for orders; orders writes orders for status."""
        self.write_job("cart", produces={"carts": ["orders"]}, sizing=self.SIZED_PRODUCER)
        self.write_job(
            "orders",
            requires=["carts"],
            produces={"orders": ["status"]},
            sizing={**self.SIZED_PRODUCER, "margin": 0.15},
        )
        self.write_job(
            "status", forward=["BASE_URL"], requires=["orders"], sizing={"margin": 0.15}
        )
        if options:
            self.write_options("5-iterations", ITERATIONS_5)
            self.write_options("5-vu-5m", CONSTANT_5_VUS_5M)

    def run_workflow_menu(self, *indices: int | None, inputs=("", "y")):
        output = io.StringIO()
        by_script = {
            "/scripts/cart.js": "|".join(f"[DATA carts] {n}" for n in range(1, 8)),
            "/scripts/orders.js": "|".join(f"[DATA orders] {n}" for n in range(1, 7)),
        }
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT_BY_SCRIPT": json.dumps(by_script)}):
            # answers="": every delete prompt reads an empty line (no).
            with self.record_menus(*indices, answers="") as calls, patch("sys.stdout", output):
                with patch("builtins.input", side_effect=list(inputs)):
                    rc = run_menu(
                        self.root, options_dir=self.root_options_dir(), state_dir=self.state_dir
                    )
        return rc, calls, output.getvalue()

    def read_state(self, name: str):
        return json.loads((self.state_dir / name).read_text(encoding="utf-8"))

    def test_workflow_menu_runs_a_sized_three_step_workflow(self) -> None:
        self.write_chain()
        # top-level (Run workflow=1), cart (0), orders (0), status (0), 5-iterations (1)
        rc, calls, text = self.run_workflow_menu(1, 0, 0, 0, 1)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][0], ["Run job", "Run workflow", "Monitoring setup"])
        self.assertTrue(calls[1][1]["title"].startswith("Step 1:\n"))
        self.assertEqual(len(calls[1][0]), 1)  # only cart starts a workflow
        self.assertEqual(calls[2][0], ["orders  reads carts"])
        self.assertEqual(calls[2][1]["title"], "Step 2, after cart:")
        self.assertEqual(calls[3][0], ["status  reads orders", "Done (2 steps)"])
        self.assertEqual(calls[3][1]["title"], "Step 3, after orders:")
        self.assertEqual(calls[4][0], ["Job default (script options)", "5-iterations", "5-vu-5m"])
        self.assertEqual(calls[4][1]["title"], "Load options for status (last step):")
        self.assertIn("[punch] no job reads what status writes; the workflow has 3 steps", text)
        for line in (
            "Punch workflow plan",
            "  step 1  cart    auto: sized for step 2 (7 iterations, 1 VU)  (writes carts)",
            "  step 2  orders  auto: sized for step 3 (6 iterations, 1 VU)  "
            "(reads carts from step 1; writes orders)",
            "  step 3  status  5-iterations  (reads orders from step 2)",
            f"  BASE_URL  k6: {DEFAULT_BASE_URL}",
        ):
            self.assertIn(line, text)
        cart, orders, status = self.fake_docker_calls()
        self.assertIn("/scripts/cart.js", cart)
        self.assertRunsWithConfig(cart, self.state_dir / "k6-config-cart.json")
        self.assertRunsWithConfig(orders, self.state_dir / "k6-config-orders.json")
        self.assertRunsWithConfig(status, self.root_options_dir() / "5-iterations.json")
        self.assertIn(f"BASE_URL={DEFAULT_BASE_URL}", status)
        self.assertEqual(self.read_state("k6-config-cart.json"), {"vus": 1, "iterations": 7})
        self.assertEqual(self.read_state("k6-config-orders.json"), {"vus": 1, "iterations": 6})
        evidence = self.read_state("punch-workflow.json")
        self.assertTrue(evidence["passed"])
        self.assertEqual(
            [step["rows"] for step in evidence["steps"]], [{"carts": 7}, {"orders": 6}, {}]
        )

    def test_done_ends_the_workflow_after_two_steps(self) -> None:
        self.write_chain()
        # top-level (1), cart (0), orders (0), Done (1), 5-iterations (1)
        rc, calls, text = self.run_workflow_menu(1, 0, 0, 1, 1)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[4][1]["title"], "Load options for orders (last step):")
        self.assertIn(
            "  step 1  cart    auto: sized for step 2 (6 iterations, 1 VU)  (writes carts)", text
        )
        self.assertEqual(len(self.fake_docker_calls()), 2)

    def test_without_data_sizing_every_step_runs_the_picked_options(self) -> None:
        self.write_chain()
        rc, _calls, _text = self.run_workflow_menu(1, 0, 0, 0, 1, inputs=("n", "y"))
        self.assertEqual(rc, 0)
        calls = self.fake_docker_calls()
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertRunsWithConfig(call, self.root_options_dir() / "5-iterations.json")
        self.assertFalse((self.state_dir / "k6-config-cart.json").exists())

    def test_without_options_the_last_step_runs_its_job_default(self) -> None:
        self.write_chain(options=False)
        # top-level (1), cart (0), orders (0), status (0); no options picker without options
        rc, calls, text = self.run_workflow_menu(1, 0, 0, 0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 4)
        self.assertIn("  step 3  status  script options  (reads orders from step 2)", text)
        # status's script options run 1 iteration: orders ceil(1 x 1.15) = 2, cart 3.
        self.assertEqual(self.read_state("k6-config-orders.json"), {"vus": 1, "iterations": 2})
        self.assertEqual(self.read_state("k6-config-cart.json"), {"vus": 1, "iterations": 3})
        *_, status = self.fake_docker_calls()
        self.assertNotIn("--config", status)

    def test_declining_the_plan_runs_nothing(self) -> None:
        self.write_chain()
        rc, _calls, text = self.run_workflow_menu(1, 0, 0, 0, 1, inputs=("", "n"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn("[punch] workflow not run.", text)
        self.assertFalse((self.state_dir / "punch-workflow.json").exists())

    def test_esc_on_a_step_picker_cancels_the_menu(self) -> None:
        self.write_chain()
        rc, calls, text = self.run_workflow_menu(1, 0, None)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn("[punch] menu canceled.", text)

    def test_a_catalog_without_links_offers_no_workflow(self) -> None:
        self.write_job("fixture")
        rc, calls, text = self.run_workflow_menu(1)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn(f"[punch] no job in {self.root} writes data another job reads", text)

    def test_interrupt_during_a_workflow_step_exits_130_with_evidence(self) -> None:
        self.write_chain()
        with patch("punch.workflow.execute_job", side_effect=KeyboardInterrupt):
            with patch("sys.stderr", io.StringIO()):
                rc, _calls, _text = self.run_workflow_menu(1, 0, 0, 0, 1)
        self.assertEqual(rc, 130)
        evidence = self.read_state("punch-workflow.json")
        self.assertEqual(evidence["outcome"], "interrupted")
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["interrupted", "skipped", "skipped"]
        )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_menu.py' -v`
Expected: the 8 new workflow tests and `test_monitoring_setup_is_a_stub` FAIL or ERROR (the top-level menu still has two entries, so index 2 and the `Run workflow` assertions fail, and some tests run out of scripted selections).

- [ ] **Step 4: Write the menu flow**

In `src/punch/menu.py`:

1. Replace `from punch.catalog import CatalogError, load_catalog` with:

```python
from punch.catalog import CatalogError, JobCatalog, load_catalog
```

2. After the `from punch.sizing import (...)` block, add:

```python
from punch.workflow import (
    AUTO,
    Load,
    WorkflowPlan,
    data_label,
    first_jobs,
    next_jobs,
    plan_workflow,
    run_workflow,
    step_label,
)
```

3. Change `_job_menu_rows` to take a heading. Its signature becomes:

```python
def _job_menu_rows(paths: List[Path], heading: str = "Available k6 jobs") -> tuple[List[str], str]:
```

and its title line becomes:

```python
    title = f"{heading}\n" + format_row(headers)
```

4. Change `_choose_options` to take a title. Its signature becomes:

```python
def _choose_options(
    job: K6Job, options_dir: Path, title: str = "Load an options preset:"
) -> Optional[Path]:
```

and its picker call becomes `choice = _select(entries, title)`.

5. Replace `_choose_top_level_action` with:

```python
def _choose_top_level_action() -> int:
    return _select(["Run job", "Run workflow", "Monitoring setup"], "[punch] Punch orchestrator.")
```

6. In `run_menu`, replace

```python
        action = _choose_top_level_action()
        if action == 1:
            return _monitoring_setup()
        return _run_job_menu(jobs_dir, options_dir, state_dir)
```

with

```python
        action = _choose_top_level_action()
        if action == 1:
            return _run_workflow_menu(jobs_dir, options_dir, state_dir)
        if action == 2:
            return _monitoring_setup()
        return _run_job_menu(jobs_dir, options_dir, state_dir)
```

7. Append to the end of `src/punch/menu.py`:

```python
def _pick_workflow(catalog: JobCatalog) -> List[str]:
    """Step 1 from first_jobs, then each next step from next_jobs, until Done or
    no job can follow."""
    first = first_jobs(catalog)
    rows, title = _job_menu_rows([catalog.jobs[name].source_path for name in first], "Step 1:")
    chosen = [first[_select(rows, title)]]
    while True:
        candidates = next_jobs(chosen, catalog)
        if not candidates:
            print(
                f"[punch] no job reads what {chosen[-1]} writes; "
                f"the workflow has {len(chosen)} steps"
            )
            return chosen
        entries = [f"{name}  reads {', '.join(datasets)}" for name, datasets in candidates]
        if len(chosen) >= 2:
            entries.append(f"Done ({len(chosen)} steps)")
        choice = _select(entries, f"Step {len(chosen) + 1}, after {chosen[-1]}:")
        if choice == len(candidates):
            return chosen
        chosen.append(candidates[choice][0])


def _workflow_base_urls(plan: WorkflowPlan) -> dict[str, str]:
    """BASE_URL per compose service, resolved as Run job resolves it, without a prompt."""
    urls: dict[str, str] = {}
    for step in plan.steps:
        service = step.job.compose_service
        if "BASE_URL" not in step.job.forward_environment or service in urls:
            continue
        url = os.environ.get("BASE_URL") or _default_base_url(service)
        if url is not None:
            urls[service] = url
    return urls


def _print_plan(plan: WorkflowPlan, base_urls: dict[str, str]) -> None:
    width = max(len(step.name) for step in plan.steps)
    print()
    print("Punch workflow plan")
    for step in plan.steps:
        print(
            f"  step {step.number}  {step.name:<{width}}  {step_label(step)}  "
            f"({data_label(step)})"
        )
    for service, url in base_urls.items():
        print(f"  BASE_URL  {service}: {url}")
    for warning in plan.warnings:
        print(f"[punch] warning: {warning}")


def _run_workflow_menu(jobs_dir: Path, options_dir: Optional[Path], state_dir: Path) -> int:
    resolved_options_dir = options_dir if options_dir is not None else jobs_dir.parent / "options"
    try:
        catalog = load_catalog(jobs_dir)
    except CatalogError as error:
        print(f"[punch] could not load the jobs in {jobs_dir}: {error}", file=sys.stderr)
        return 1
    if not first_jobs(catalog):
        print(f"[punch] no job in {jobs_dir} writes data another job reads")
        return 1

    print()
    jobs = _pick_workflow(catalog)
    last = catalog.jobs[jobs[-1]]
    picked = _choose_options(
        last, resolved_options_dir, f"Load options for {last.name} (last step):"
    )
    answer = _prompt("Data sizing (each earlier step is sized for the next)? (Y/n)", default="y")
    sizing = not answer.lower().startswith("n")
    loads: List[Load] = (
        [*([AUTO] * (len(jobs) - 1)), picked] if sizing else [picked] * len(jobs)
    )
    plan = plan_workflow(jobs, loads, catalog)
    if plan.errors:
        for error in plan.errors:
            print(f"[punch] {error}", file=sys.stderr)
        return 1
    base_urls = _workflow_base_urls(plan)
    _print_plan(plan, base_urls)
    answer = _prompt(f"Run these {len(plan.steps)} steps? (y/N)", default="n")
    if not answer.lower().startswith("y"):
        print("[punch] workflow not run.")
        return 0
    print()
    return run_workflow(
        plan, catalog, environment=os.environ, base_urls=base_urls, state_dir=state_dir
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_menu.py' -v`
Expected: `OK`.

Run the full suite:
`PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_*.py'`
Expected: `OK` (365 tests).

- [ ] **Step 6: Format and lint**

Run: `uvx ruff@0.7.4 format src tests && uvx ruff@0.7.4 check src tests`
Expected: `All checks passed!`

- [ ] **Step 7: Check that no job or dataset name entered the engine**

Run: `grep -n -E '"(cart|carts|orders|http-[a-z-]+)"' src/punch/*.py`
Expected: no output. (`status` is left out of the pattern: it is also an evidence key, `record["status"]`.)

- [ ] **Step 8: Commit**

```bash
git add src/punch/menu.py tests/test_menu.py
git commit -m "feat(menu): run a Punch workflow from the menu's Run workflow option"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/jobs/validation.md`
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/specs/spec-target-data-sizing.md` (status line only)

**Interfaces:**
- Consumes: the behavior shipped in Tasks 1-4 and the exact strings in Global Constraints.
- Produces: no code.

- [ ] **Step 1: README**

In `README.md`:

1. Replace the `punch menu` row of the Commands table with:

```markdown
| `punch menu [jobs-dir]` | Pick and run a job, or a Punch workflow of jobs, interactively (default: `jobs/k6/`) |
```

2. In `### Datasets`, add this bullet after the `**Sizing for a target.**` bullet:

```markdown
- **Workflows (menu only).** `Run workflow` builds a Punch workflow one step
  at a time: step 1 from the jobs that need no earlier step, then only jobs
  that read what the previous step writes, with `Done` from step 3 on. The
  last step runs the picked options. With data sizing on (the default), every
  earlier step is sized for the step after it, resolving from the last step
  back; with it off, every step runs the picked options. Each step is one
  Compose run that reads its data from the step that wrote it in this
  workflow, and the workflow stops at the first failure. Spec:
  [spec-menu-workflow.md](docs/specs/spec-menu-workflow.md).
```

3. In `## Artifacts`, append this sentence to the first paragraph:

```markdown
A menu workflow writes `reports/state/punch-workflow.json` instead.
```

4. In the `## Docs` table, change `menu workflows (proposed)` to `menu workflows`.

- [ ] **Step 2: validation.md**

In `docs/jobs/validation.md`:

1. Under `## Run evidence: \`reports/state/punch-run.json\``, replace these two lines:

```markdown
Written by `src/punch/__main__.py` on every `punch run`, pass or fail. The
menu prints results to the terminal and writes no evidence.
```

with:

```markdown
Written by `src/punch/__main__.py` on every `punch run`, pass or fail. The
menu's `Run job` prints results to the terminal and writes no evidence; its
`Run workflow` writes
[`punch-workflow.json`](#workflow-evidence-reportsstatepunch-workflowjson).
```

2. Insert this section directly above `## Other artifacts`:

````markdown
## Workflow evidence: `reports/state/punch-workflow.json`

Written by `punch.workflow.run_workflow` for the menu's `Run workflow`: before
step 1, after every step, and on interruption. Paths are relative to the
current directory when beneath it, absolute otherwise.

```json
{
  "version": 1,
  "outcome": "passed",
  "passed": true,
  "jobs": ["http-cart", "http-orders", "http-orders-status"],
  "steps": [
    {"step": 1, "job": "http-cart", "load": "auto",
     "label": "auto: sized for step 2 (7 iterations, 1 VU)",
     "config": "reports/state/k6-config-http-cart.json", "sizing": {"...": "..."},
     "produce": ["carts"], "reads": {}, "builtIn": [],
     "status": "passed", "exitCode": 0, "failure": null, "rows": {"carts": 7}}
  ],
  "warnings": []
}
```

| Key | Meaning |
|---|---|
| `outcome` | `running`, `passed`, `failed`, or `interrupted` |
| `passed` | `true` only when `outcome` is `passed` |
| `steps[].load` | `auto`, `job default`, or the options file |
| `steps[].label` | The plan's load text for the step |
| `steps[].config` | The file passed as `--config`; `null` for script options |
| `steps[].sizing` | For a sized step: the `sizing` keys `punch-run.json` records |
| `steps[].status` | `pending`, `passed`, `failed`, `skipped`, or `interrupted` |
| `steps[].exitCode` | The k6 container's code; `null` when no container started |
| `steps[].rows` | Rows written per dataset |
| `warnings` | Plan warnings, then `rows repeat` warnings |

A menu workflow change is verified when this file shows `"passed": true`.
````

3. In the `## Other artifacts` table, change the `k6-config-<producer>.json` row's second cell to `Punch, for a sized producer run or a sized workflow step`.

- [ ] **Step 3: CLAUDE.md layout row**

In `CLAUDE.md`, in the `src/punch/` row of the Layout table, insert `` `workflow` (Punch workflows: pure planning, `run_workflow`), `` after `` `sizing` (pure), ``.

- [ ] **Step 4: CHANGELOG**

In `CHANGELOG.md`, add this bullet first under `## [Unreleased]` → `### Added`:

```markdown
- Menu workflows: `punch menu` → `Run workflow` picks jobs one step at a
  time, sizes every earlier step for the next one (data sizing, on by
  default), runs one Compose run per step, and writes
  `reports/state/punch-workflow.json`. Planning lives in `punch.workflow`.
```

- [ ] **Step 5: Supersede note in the sizing spec**

In `docs/specs/spec-target-data-sizing.md`, append to the status blockquote (after `[`spec-native-k6-config.md`](spec-native-k6-config.md).`):

```markdown
> Superseded in part <today, from `date +%F`>: the menu's `Run workflow`
> sizes chained steps; see [`spec-menu-workflow.md`](spec-menu-workflow.md).
```

Replace `<today, from \`date +%F\`>` with the output of `date +%F`.

- [ ] **Step 6: Check the docs**

Run: `PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_ci_contract.py'`
Expected: `OK`.

Run: `git diff -U0 | grep '^+' | grep -n -P '\x{2014}|\x{2013}| -- '`
Expected: no output (no new em dashes, en dashes, or ` -- `).

- [ ] **Step 7: Commit**

```bash
git add README.md docs/jobs/validation.md CLAUDE.md CHANGELOG.md docs/specs/spec-target-data-sizing.md
git commit -m "docs: document the menu's Run workflow and punch-workflow.json"
```

---

### Task 6: Runtime evidence

CLAUDE.md rule 3 needs a live run. The bundled jobs declare no datasets, so the run uses the parent repository's catalog. The menu needs a real terminal: the operator runs Step 2; an agent cannot drive the arrow-key pickers.

**Files:**
- Modify: `docs/specs/spec-menu-workflow.md` (status line only)

**Interfaces:**
- Consumes: the parent repository's `./dev up`, its `tests/performance/k6/jobs/`, and `infra/docker/compose.performance.yaml`.
- Produces: `reports/state/punch-workflow.json` inside `vendor/punch` with `"passed": true`.

- [ ] **Step 1: Start the parent repository's stack** (from the parent repository root)

```bash
./dev up
docker compose -f infra/docker/compose.performance.yaml build k6
```

Expected: `./dev up` ends healthy; the k6 image builds.

- [ ] **Step 2: Run the workflow from the menu** (operator, from the parent repository root)

```bash
PYTHONPATH=vendor/punch/src uv run --no-project --python 3.11 \
  --with-requirements vendor/punch/requirements.txt \
  python -m punch menu tests/performance/k6/jobs
```

Pick `Run workflow` → `http-cart` → `http-orders` → `http-orders-status` → `5-iterations`, press Enter at the data sizing prompt, and answer `y` at `Run these 3 steps?`. Answer `n` at every delete prompt.

Expected plan lines:

```text
  step 1  http-cart           auto: sized for step 2 (7 iterations, 1 VU)  (writes carts)
  step 2  http-orders         auto: sized for step 3 (6 iterations, 1 VU)  (reads carts from step 1; writes orders)
  step 3  http-orders-status  5-iterations  (reads orders from step 2)
```

- [ ] **Step 3: Check the evidence**

```bash
python3 -c "import json; e = json.load(open('vendor/punch/reports/state/punch-workflow.json')); print(e['passed'], [s['rows'] for s in e['steps']])"
```

Expected: `True [{'carts': 7}, {'orders': 6}, {}]`.

- [ ] **Step 4: Stop the stack**

```bash
./dev down
```

- [ ] **Step 5: Mark the spec implemented** (from `vendor/punch`)

In `docs/specs/spec-menu-workflow.md`, replace `> **Status:** Proposed 2026-10-19.` with `> **Status:** Implemented <the output of date +%F>.`

```bash
git add docs/specs/spec-menu-workflow.md
git commit -m "docs(spec): mark menu workflows implemented"
```

Pushing Punch and bumping the parent repository's `vendor/punch` pointer are separate decisions for the owner: push Punch first, then bump the pointer, or the parent repository's CI checkout fails.

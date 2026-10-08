# Target Data Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator size a producer run for a target run — Punch
estimates the rows the target reads from its load shape, adds the target's
margin, and runs the producer with enough iterations and VUs to write them —
from the `punch` menu and from `punch run --size-for`.

**Architecture:** Workflow YAML gains an optional `spec.sizing` block
(`iterationSeconds`, `maxSeconds`, `margin`). A new stdlib-only
`punch.sizing` module owns eligibility, the math (exact decimals), the
producer environment, messages, and evidence. `menu.py` adds an options-mode
picker, a target picker, and a target-preset picker, and drops the forced
write after a producer switch. `__main__.py` adds `--size-for`.
`punch.data_plan` and `punch.execution` are not touched.

**Tech Stack:** Python 3.10+, PyYAML, simple-term-menu, rich; `unittest`;
`fractions.Fraction` for exact ceilings.

**Spec:** `docs/specs/spec-target-data-sizing.md` (rev 2).

## Global Constraints

- At most one `docker compose run` per invocation; sizing never chains a second run.
- No workflow or dataset name literal in `src/punch/*.py`. The only new literals are the load-shape names `ITERATIONS`, `VUS`, `DURATION`, defined once in `punch.sizing`.
- `punch.sizing` imports only the standard library, `punch.catalog`, and `punch.workflow`.
- `punch.data_plan` and `punch.execution` are unchanged.
- Every ceiling is taken on exact decimals: `Fraction(str(x))`, never binary floats (`ceil(20 × 1.1) = 22`).
- `spec.sizing` is strict: unknown keys rejected; `iterationSeconds` and `maxSeconds` numbers `> 0`; `margin` number `>= 0`, default `0`; booleans and non-finite numbers rejected.
- A target shape only counts names the target forwards; empty values count as unset; `ITERATIONS` beats `DURATION`.
- Producer environment: `ITERATIONS=N`, `VUS=V`, `DURATION` removed; `V = min(N, max(1, ceil(N × iterationSeconds_p / maxSeconds_p)))`.
- A shortfall (produced rows < rows needed) warns and never changes the exit code.
- Commits carry no AI attribution (no `Co-Authored-By`, no "Generated with").

## Review Focus

1. **Host already exports a load shape** (e.g. `export $(jq … options/5-vu-5m.json)` from the parent README) when a producer is sized — expected: the producer gets exactly `ITERATIONS=N VUS=V` and no `DURATION` (Task 4 `test_sized_run_overrides_shape_and_writes_without_asking`; Task 5 `test_size_for_overrides_shape_and_writes_dataset` both set `DURATION` in the environment first).
2. **Operator answers `n` to the write prompt after a switch** — expected: no data file and no misleading `ready; run <origin> next` hint (Task 4 `test_declined_switch_write_prints_no_ready_hint`).
3. **Producer has sizing pairs but `options/` is missing or empty** — expected: no options-mode picker, today's flow (Task 4 `test_no_presets_means_no_mode_picker`).
4. **Junk load values** (`"lots"`, `"0"`, `""`, `DURATION=5`) — expected: a not-estimable reason, never a crash; empty means unset (Task 2 `test_invalid_values_are_reasons_not_crashes`, `test_empty_values_count_as_unset`).
5. **Non-finite or boolean YAML numbers** (`margin: .inf`, `maxSeconds: true`) — expected: `WorkflowError` at load (Task 1 `test_rejects_invalid_sizing`).

## Test command

```bash
cd vendor/punch
test -x /tmp/punch-venv/bin/python || { python3 -m venv /tmp/punch-venv && /tmp/punch-venv/bin/pip install -q -r requirements.txt; }
```

`PY` = `PYTHONPATH=src /tmp/punch-venv/bin/python`. Full suite:
`PY -m unittest discover -s tests -p 'test_*.py'` (baseline: 225 tests OK).

All Punch tasks (1–6) run inside `vendor/punch` on branch
`feat/target-data-sizing` (already created; the spec commit is on it).

## File Structure

| File | Responsibility |
|---|---|
| `src/punch/workflow.py` (modify) | `Sizing` dataclass; strict `spec.sizing` parse; `K6Workflow.sizing` |
| `src/punch/sizing.py` (create) | eligibility, target shape, rows needed, producer plan, producer environment, summary/warning/hint text, evidence dict — pure |
| `src/punch/menu.py` (modify) | options-mode / target / target-preset pickers; sized produce; environment override; post-run shortfall + hint; switch parity |
| `src/punch/__main__.py` (modify) | `--size-for`; implied `--produce`; evidence `sizing`; flag dropped after a switch |
| `tests/test_workflow.py`, `tests/test_sizing.py` (create), `tests/test_menu.py`, `tests/test_cli.py` | tests per task |
| `README.md`, `docs/workflows/validation.md`, `CLAUDE.md`, `CHANGELOG.md` | Punch docs |
| Parent: `tests/performance/k6/workflows/*.yaml`, `tests/performance/k6/README.md`, `docs/performance/orchestrator.md`, `docs/next-steps/sizing-duration-targets.md`, `docs/next-steps/README.md` | measured `spec.sizing`, docs, follow-up |

---

### Task 1: `spec.sizing` in the workflow schema

**Files:**
- Modify: `src/punch/workflow.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Produces: `workflow.Sizing(iteration_seconds: float | None = None, max_seconds: float | None = None, margin: float = 0.0)` (frozen dataclass); `K6Workflow.sizing: Sizing | None = None` (last field).

- [ ] **Step 1: Write the failing tests** — in `tests/test_workflow.py`, change the import to
  `from punch.workflow import Sizing, WorkflowError, load_workflow` and add to `LoadWorkflowTests`:

```python
    SIZING_ANCHOR = "    requires: [carts]\n"

    def with_sizing(self, block: str) -> tuple[str, str]:
        return (self.SIZING_ANCHOR, self.SIZING_ANCHOR + block)

    def test_sizing_defaults_to_none(self) -> None:
        self.assertIsNone(load_workflow(self.workflow_path).sizing)

    def test_reads_sizing(self) -> None:
        self.write_workflow(
            self.workflow_path,
            self.with_sizing(
                "  sizing:\n    iterationSeconds: 1.5\n    maxSeconds: 270\n    margin: 0.15\n"
            ),
        )
        self.assertEqual(
            load_workflow(self.workflow_path).sizing,
            Sizing(iteration_seconds=1.5, max_seconds=270, margin=0.15),
        )

    def test_empty_sizing_is_a_target_with_zero_margin(self) -> None:
        self.write_workflow(self.workflow_path, self.with_sizing("  sizing: {}\n"))
        self.assertEqual(load_workflow(self.workflow_path).sizing, Sizing())

    def test_rejects_invalid_sizing(self) -> None:
        cases = [
            ("  sizing:\n", "spec.sizing must be a mapping"),
            ("  sizing:\n    unknown: 1\n", "unknown field spec.sizing.unknown"),
            ("  sizing:\n    margin: -1\n", "spec.sizing.margin must be 0 or greater"),
            ("  sizing:\n    iterationSeconds: 0\n",
             "spec.sizing.iterationSeconds must be greater than 0"),
            ("  sizing:\n    maxSeconds: -5\n", "spec.sizing.maxSeconds must be greater than 0"),
            ("  sizing:\n    maxSeconds: true\n", "spec.sizing.maxSeconds must be a number"),
            ('  sizing:\n    iterationSeconds: "1"\n',
             "spec.sizing.iterationSeconds must be a number"),
            ("  sizing:\n    margin: .inf\n", "spec.sizing.margin must be a number"),
            ("  sizing:\n    iterationSeconds: .nan\n",
             "spec.sizing.iterationSeconds must be a number"),
        ]
        for block, message in cases:
            with self.subTest(block=block):
                self.assertWorkflowError(message, self.with_sizing(block))
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_workflow -v`
Expected: ImportError (`cannot import name 'Sizing'`), so the module errors.

- [ ] **Step 3: Implement** — `src/punch/workflow.py`:

Add `import math` after `import re`. After the `SummaryOutput` dataclass add:

```python
@dataclass(frozen=True)
class Sizing:
    """`spec.sizing`: pace and budget as a sized producer, margin as a sizing target."""

    iteration_seconds: float | None = None
    max_seconds: float | None = None
    margin: float = 0.0
```

In `K6Workflow`, add as the last field (after `description: str = ""`):

```python
    sizing: Sizing | None = None
```

Change `SPEC_KEYS` and add `SIZING_KEYS` next to it:

```python
SPEC_KEYS = {"workingDirectory", "compose", "k6", "environment", "outputs", "data", "sizing"}
SIZING_KEYS = {"iterationSeconds", "maxSeconds", "margin"}
```

Add after `_data_spec`:

```python
def _sizing_number(sizing: dict[str, Any], key: str, *, allow_zero: bool) -> float | None:
    if key not in sizing:
        return None
    value = sizing[key]
    field = f"spec.sizing.{key}"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WorkflowError(f"{field} must be a number")
    if allow_zero and value < 0:
        raise WorkflowError(f"{field} must be 0 or greater")
    if not allow_zero and value <= 0:
        raise WorkflowError(f"{field} must be greater than 0")
    return value


def _sizing(value: Any) -> Sizing:
    sizing = _allowed_keys(value, SIZING_KEYS, "spec.sizing")
    margin = _sizing_number(sizing, "margin", allow_zero=True)
    return Sizing(
        iteration_seconds=_sizing_number(sizing, "iterationSeconds", allow_zero=False),
        max_seconds=_sizing_number(sizing, "maxSeconds", allow_zero=False),
        margin=0.0 if margin is None else margin,
    )
```

In `load_workflow`, after the `data = …` line add
`sizing = _sizing(spec["sizing"]) if "sizing" in spec else None`
and pass `sizing=sizing,` as the last `K6Workflow(...)` argument.

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_workflow -v` → all OK.
Run the full suite → 225 + 4 OK.

- [ ] **Step 5: Commit**

```bash
git add src/punch/workflow.py tests/test_workflow.py
git commit -m "feat(workflow): optional spec.sizing pace, budget, and margin"
```

---

### Task 2: `punch.sizing` — eligibility and math

**Files:**
- Create: `src/punch/sizing.py`
- Create: `tests/test_sizing.py`

**Interfaces:**
- Consumes: `Sizing`, `K6Workflow.sizing` (Task 1); `WorkflowCatalog.workflows` (existing).
- Produces (all in `punch.sizing`):
  - constants `ITERATIONS = "ITERATIONS"`, `VUS = "VUS"`, `DURATION = "DURATION"`, `SHAPE_NAMES`;
  - `class SizingError(ValueError)`;
  - `parse_duration(text: str) -> Fraction` (raises `ValueError`);
  - `is_sizable_producer(workflow: K6Workflow) -> bool`;
  - `sizing_pairs(producer: K6Workflow, catalog: WorkflowCatalog) -> tuple[tuple[str, str], ...]` — `(dataset, target)`;
  - `target_shape(target: K6Workflow, source: Mapping[str, str]) -> dict[str, str]`;
  - `rows_needed(target: K6Workflow, source: Mapping[str, str]) -> int` (raises `SizingError`);
  - `@dataclass(frozen=True) SizingPlan(producer, target, datasets: tuple[str, ...], shape: Mapping[str, str], rows_needed: int, margin: float, iterations: int, vus: int, estimated_seconds: float, preset: str | None = None)`;
  - `size_producer(producer, target_name: str, catalog, source: Mapping[str, str], *, preset: str | None = None) -> SizingPlan` (raises `SizingError`);
  - `producer_environment(environment: Mapping[str, str], plan: SizingPlan) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_sizing.py`:

```python
from __future__ import annotations

import sys
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.sizing import (
    SizingError,
    is_sizable_producer,
    parse_duration,
    producer_environment,
    rows_needed,
    size_producer,
    sizing_pairs,
)

SHAPE = ["ITERATIONS", "VUS", "DURATION"]
PRODUCER_SIZING = {"iterationSeconds": 1, "maxSeconds": 270}
TARGET_SIZING = {"iterationSeconds": 2, "margin": 0.15}


def workflow_yaml(
    name: str,
    *,
    forward: list[str],
    produces: dict[str, list[str]] | None = None,
    requires: list[str] | None = None,
    sizing: dict | None = None,
) -> str:
    lines = [
        "apiVersion: punch/v1", "kind: K6Workflow", "metadata:", f"  name: {name}",
        "spec:", "  workingDirectory: .", "  compose:", "    file: docker-compose.yml",
        "    service: k6", "  k6:", f"    script: /scripts/{name}.js",
        "  environment:", f"    forward: [{', '.join(forward)}]",
        "  data:", "    directory: data", "    mountedAt: /scripts/data",
    ]
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
    if sizing is not None:
        lines.append("  sizing:" if sizing else "  sizing: {}")
        lines += [f"    {key}: {value}" for key, value in sizing.items()]
    return "\n".join(lines) + "\n"


class SizingCase(unittest.TestCase):
    """producer → carts → consumer; both sized and forwarding the full shape."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "docker-compose.yml").touch()
        self.write_producer()
        self.write_consumer()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, **kwargs) -> None:
        (self.root / f"{name}.yaml").write_text(workflow_yaml(name, **kwargs), encoding="utf-8")

    def write_producer(self, *, forward=SHAPE, sizing=PRODUCER_SIZING, targets=("consumer",)) -> None:
        self.write("producer", forward=forward, produces={"carts": list(targets)}, sizing=sizing)

    def write_consumer(self, *, forward=SHAPE, sizing=TARGET_SIZING, name="consumer") -> None:
        self.write(name, forward=forward, requires=["carts"], sizing=sizing)

    def size(self, source, target="consumer", **kwargs):
        catalog = load_catalog(self.root)
        return size_producer(catalog.workflows["producer"], target, catalog, source, **kwargs)

    def assertNotEstimable(self, message: str, source, target="consumer") -> None:
        with self.assertRaisesRegex(SizingError, message):
            self.size(source, target)


class ParseDurationTests(unittest.TestCase):
    def test_parses_k6_durations(self) -> None:
        cases = {"5m": 300, "1h30m": 5400, "90s": 90, "1m30s": 90, "1.5m": 90,
                 "500ms": Fraction(1, 2)}
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), seconds)

    def test_rejects_other_strings(self) -> None:
        for text in ("", "5", "5x", "m", "0s", "5m ", "-5m", "5 m"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_duration(text)


class SizingMathTests(SizingCase):
    def test_iterations_shape_is_rows_needed(self) -> None:
        plan = self.size({"ITERATIONS": "50"})
        self.assertEqual(plan.datasets, ("carts",))
        self.assertEqual(dict(plan.shape), {"ITERATIONS": "50"})
        self.assertEqual((plan.rows_needed, plan.iterations, plan.vus), (50, 58, 1))
        self.assertEqual(plan.margin, 0.15)
        self.assertEqual(plan.estimated_seconds, 58.0)

    def test_iterations_wins_over_duration(self) -> None:
        plan = self.size({"ITERATIONS": "5", "VUS": "5", "DURATION": "5m"})
        self.assertEqual(plan.rows_needed, 5)
        self.assertEqual(dict(plan.shape), {"ITERATIONS": "5", "VUS": "5", "DURATION": "5m"})

    def test_duration_shape_uses_target_pace(self) -> None:
        plan = self.size({"VUS": "5", "DURATION": "5m"})
        # ceil(5 × 300 / 2) = 750; ceil(750 × 1.15) = 863; ceil(863 × 1 / 270) = 4
        self.assertEqual((plan.rows_needed, plan.iterations, plan.vus), (750, 863, 4))
        self.assertEqual(plan.estimated_seconds, 215.75)

    def test_ceilings_use_exact_decimals(self) -> None:
        self.write_consumer(sizing={"margin": 0.1})
        # 20 × 1.1 is 22.000000000000004 in binary floats
        self.assertEqual(self.size({"ITERATIONS": "20"}).iterations, 22)

    def test_vus_never_exceed_iterations(self) -> None:
        self.write_producer(sizing={"iterationSeconds": 600, "maxSeconds": 270})
        self.write_consumer(sizing={"margin": 0})
        plan = self.size({"ITERATIONS": "2"})
        self.assertEqual((plan.iterations, plan.vus), (2, 2))
        self.assertEqual(plan.estimated_seconds, 600.0)

    def test_shape_only_counts_forwarded_names(self) -> None:
        self.write_consumer(forward=["ITERATIONS", "VUS"])
        self.assertNotEstimable("consumer does not forward DURATION", {"VUS": "5", "DURATION": "5m"})

    def test_target_forwarding_neither(self) -> None:
        self.write_consumer(forward=["VUS"])
        self.assertNotEstimable("consumer forwards neither ITERATIONS nor DURATION", {"ITERATIONS": "5"})

    def test_shape_sets_neither(self) -> None:
        self.assertNotEstimable("shape sets neither ITERATIONS nor DURATION", {"VUS": "5"})
        self.assertNotEstimable("shape sets neither ITERATIONS nor DURATION", {})

    def test_duration_needs_vus(self) -> None:
        self.assertNotEstimable("DURATION needs VUS", {"DURATION": "5m"})

    def test_duration_needs_target_pace(self) -> None:
        self.write_consumer(sizing={"margin": 0.15})
        self.assertNotEstimable(
            "consumer declares no sizing.iterationSeconds", {"VUS": "5", "DURATION": "5m"}
        )
        self.assertEqual(self.size({"ITERATIONS": "5"}).rows_needed, 5)

    def test_invalid_values_are_reasons_not_crashes(self) -> None:
        self.assertNotEstimable('invalid ITERATIONS "0"', {"ITERATIONS": "0"})
        self.assertNotEstimable('invalid ITERATIONS "lots"', {"ITERATIONS": "lots"})
        self.assertNotEstimable('invalid VUS "x"', {"VUS": "x", "DURATION": "5m"})
        self.assertNotEstimable('invalid DURATION "5"', {"VUS": "5", "DURATION": "5"})

    def test_empty_values_count_as_unset(self) -> None:
        plan = self.size({"ITERATIONS": "", "VUS": "5", "DURATION": "5m"})
        self.assertEqual(plan.rows_needed, 750)
        self.assertNotIn("ITERATIONS", plan.shape)

    def test_rows_needed_reads_the_raw_source(self) -> None:
        target = load_catalog(self.root).workflows["consumer"]
        self.assertEqual(rows_needed(target, {"ITERATIONS": "7", "UNRELATED": "x"}), 7)

    def test_preset_is_carried(self) -> None:
        self.assertEqual(self.size({"ITERATIONS": "5"}, preset="5-iterations").preset, "5-iterations")


class SizingEligibilityTests(SizingCase):
    def test_producer_needs_pace_budget_and_shape_forwarding(self) -> None:
        catalog = load_catalog(self.root)
        self.assertTrue(is_sizable_producer(catalog.workflows["producer"]))
        for kwargs in (
            {"sizing": {"iterationSeconds": 1}},
            {"sizing": {"maxSeconds": 270}},
            {"sizing": None},
            {"forward": ["VUS", "DURATION"]},
            {"forward": ["ITERATIONS", "DURATION"]},
        ):
            with self.subTest(**{key: str(value) for key, value in kwargs.items()}):
                self.write_producer(**kwargs)
                self.assertNotEstimable(
                    "producer is not a sizable producer: needs sizing.iterationSeconds, "
                    "sizing.maxSeconds, and forwarded ITERATIONS and VUS",
                    {"ITERATIONS": "5"},
                )

    def test_unknown_and_unsized_targets(self) -> None:
        self.assertNotEstimable("producer does not produce data for nope", {"ITERATIONS": "5"}, "nope")
        self.write_consumer(sizing=None)
        self.assertNotEstimable("consumer declares no spec.sizing", {"ITERATIONS": "5"})

    def test_sizing_pairs_skip_unsized_targets(self) -> None:
        self.write_producer(targets=("consumer", "other"))
        self.write_consumer(name="other", sizing=None)
        catalog = load_catalog(self.root)
        self.assertEqual(sizing_pairs(catalog.workflows["producer"], catalog), (("carts", "consumer"),))
        self.assertEqual(sizing_pairs(catalog.workflows["consumer"], catalog), ())

    def test_non_sizable_producer_has_no_pairs(self) -> None:
        self.write_producer(sizing=None)
        catalog = load_catalog(self.root)
        self.assertEqual(sizing_pairs(catalog.workflows["producer"], catalog), ())


class ProducerEnvironmentTests(SizingCase):
    def test_overrides_shape_and_drops_duration(self) -> None:
        plan = self.size({"ITERATIONS": "50"})
        environment = {"DURATION": "5m", "VUS": "9", "BASE_URL": "http://x"}
        self.assertEqual(
            producer_environment(environment, plan),
            {"BASE_URL": "http://x", "ITERATIONS": "58", "VUS": "1"},
        )
        self.assertEqual(environment["DURATION"], "5m")  # input untouched


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_sizing -v`
Expected: `ModuleNotFoundError: No module named 'punch.sizing'`.

- [ ] **Step 3: Implement** — create `src/punch/sizing.py`:

```python
"""Size a producer run for the rows a target run reads.

Standard-library only. Pace, budget, and margin come from each workflow's
`spec.sizing`; producer → target links from `spec.data.produces[].targets`.
The load shape is three environment names — a convention like `BASE_URL`:
Punch reads a target's shape only from names it forwards and writes a
producer's shape only through names it forwards.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from punch.catalog import WorkflowCatalog
from punch.workflow import K6Workflow

ITERATIONS = "ITERATIONS"
VUS = "VUS"
DURATION = "DURATION"
SHAPE_NAMES = (ITERATIONS, VUS, DURATION)

_DURATION = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h))+")
_DURATION_GROUP = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNIT_SECONDS = {
    "ms": Fraction(1, 1000), "s": Fraction(1), "m": Fraction(60), "h": Fraction(3600),
}
_POSITIVE_INT = re.compile(r"[1-9][0-9]*")


class SizingError(ValueError):
    """Why a producer cannot be sized for a target with this load shape."""


@dataclass(frozen=True)
class SizingPlan:
    producer: K6Workflow
    target: K6Workflow
    datasets: tuple[str, ...]
    shape: Mapping[str, str]
    rows_needed: int
    margin: float
    iterations: int
    vus: int
    estimated_seconds: float
    preset: str | None = None


def _exact(value: float) -> Fraction:
    # str() first: Fraction(0.1) is the binary float, Fraction("0.1") is 1/10.
    return Fraction(str(value))


def parse_duration(text: str) -> Fraction:
    """Seconds in a k6 duration string such as `5m`, `1h30m`, or `500ms`."""
    if not _DURATION.fullmatch(text):
        raise ValueError(f"invalid duration {text!r}")
    seconds = sum(
        (Fraction(number) * _UNIT_SECONDS[unit] for number, unit in _DURATION_GROUP.findall(text)),
        Fraction(0),
    )
    if seconds <= 0:
        raise ValueError(f"invalid duration {text!r}")
    return seconds


def _positive_int(name: str, value: str) -> int:
    if not _POSITIVE_INT.fullmatch(value):
        raise SizingError(f'invalid {name} "{value}"')
    return int(value)


def is_sizable_producer(workflow: K6Workflow) -> bool:
    sizing = workflow.sizing
    return (
        sizing is not None
        and sizing.iteration_seconds is not None
        and sizing.max_seconds is not None
        and ITERATIONS in workflow.forward_environment
        and VUS in workflow.forward_environment
    )


def sizing_pairs(producer: K6Workflow, catalog: WorkflowCatalog) -> tuple[tuple[str, str], ...]:
    """(dataset, target) for every sized target of a sizable producer, in YAML order."""
    if producer.data is None or not is_sizable_producer(producer):
        return ()
    return tuple(
        (product.dataset, target)
        for product in producer.data.produces
        for target in product.targets
        if catalog.workflows[target].sizing is not None
    )


def target_shape(target: K6Workflow, source: Mapping[str, str]) -> dict[str, str]:
    """The load-shape names `source` sets and `target` forwards; empty values are unset."""
    return {
        name: str(source[name])
        for name in SHAPE_NAMES
        if name in target.forward_environment and source.get(name)
    }


def rows_needed(target: K6Workflow, source: Mapping[str, str]) -> int:
    """Rows one target run reads (one per iteration), or a SizingError reason."""
    forwarded = target.forward_environment
    if ITERATIONS not in forwarded and DURATION not in forwarded:
        raise SizingError(f"{target.name} forwards neither ITERATIONS nor DURATION")
    shape = target_shape(target, source)
    if ITERATIONS in shape:
        return _positive_int(ITERATIONS, shape[ITERATIONS])
    if DURATION not in shape:
        dropped = [
            name for name in (ITERATIONS, DURATION)
            if source.get(name) and name not in forwarded
        ]
        if dropped:
            raise SizingError(f"{target.name} does not forward {' or '.join(dropped)}")
        raise SizingError("shape sets neither ITERATIONS nor DURATION")
    if VUS not in shape:
        raise SizingError("DURATION needs VUS")
    if target.sizing is None or target.sizing.iteration_seconds is None:
        raise SizingError(f"{target.name} declares no sizing.iterationSeconds")
    vus = _positive_int(VUS, shape[VUS])
    try:
        seconds = parse_duration(shape[DURATION])
    except ValueError:
        raise SizingError(f'invalid DURATION "{shape[DURATION]}"') from None
    return math.ceil(vus * seconds / _exact(target.sizing.iteration_seconds))


def size_producer(
    producer: K6Workflow,
    target_name: str,
    catalog: WorkflowCatalog,
    source: Mapping[str, str],
    *,
    preset: str | None = None,
) -> SizingPlan:
    if not is_sizable_producer(producer):
        raise SizingError(
            f"{producer.name} is not a sizable producer: needs sizing.iterationSeconds, "
            "sizing.maxSeconds, and forwarded ITERATIONS and VUS"
        )
    assert producer.sizing is not None and producer.data is not None
    datasets = tuple(
        product.dataset for product in producer.data.produces if target_name in product.targets
    )
    if not datasets:
        raise SizingError(f"{producer.name} does not produce data for {target_name}")
    target = catalog.workflows[target_name]
    if target.sizing is None:
        raise SizingError(f"{target_name} declares no spec.sizing")
    needed = rows_needed(target, source)
    iterations = math.ceil(needed * (1 + _exact(target.sizing.margin)))
    pace = _exact(producer.sizing.iteration_seconds)
    budget = _exact(producer.sizing.max_seconds)
    vus = min(iterations, max(1, math.ceil(iterations * pace / budget)))
    return SizingPlan(
        producer=producer,
        target=target,
        datasets=datasets,
        shape=target_shape(target, source),
        rows_needed=needed,
        margin=target.sizing.margin,
        iterations=iterations,
        vus=vus,
        estimated_seconds=float(iterations * pace / vus),
        preset=preset,
    )


def producer_environment(environment: Mapping[str, str], plan: SizingPlan) -> dict[str, str]:
    """A copy carrying the sized producer shape; DURATION is dropped so ITERATIONS rules."""
    sized = {name: value for name, value in environment.items() if name != DURATION}
    sized[ITERATIONS] = str(plan.iterations)
    sized[VUS] = str(plan.vus)
    return sized
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_sizing -v` → all OK. Full suite → OK.

- [ ] **Step 5: Commit**

```bash
git add src/punch/sizing.py tests/test_sizing.py
git commit -m "feat(sizing): size a producer run for a target's rows"
```

---

### Task 3: `punch.sizing` — messages and evidence

**Files:**
- Modify: `src/punch/sizing.py`
- Modify: `tests/test_sizing.py`

**Interfaces:**
- Consumes: `SizingPlan`, `size_producer` (Task 2).
- Produces:
  - `summary_lines(plan: SizingPlan) -> list[str]`;
  - `input_warnings(plan: SizingPlan, row_counts: Mapping[str, int]) -> list[str]`;
  - `shortfall(plan: SizingPlan, produced: Mapping[str, int]) -> list[str]`;
  - `next_hint(plan: SizingPlan, produced: Mapping[str, int]) -> str`;
  - `sizing_evidence(plan: SizingPlan, produced: Mapping[str, int]) -> dict`.

- [ ] **Step 1: Write the failing tests** — extend the `from punch.sizing import (...)` list in `tests/test_sizing.py` with `input_warnings, next_hint, shortfall, sizing_evidence, summary_lines`, and add before `if __name__ == "__main__":`:

```python
class SizingTextTests(SizingCase):
    def test_summary_lines_with_preset(self) -> None:
        plan = self.size({"ITERATIONS": "5"}, preset="5-iterations")
        self.assertEqual(summary_lines(plan), [
            "[punch] sizing producer for consumer (5-iterations)",
            "  rows needed   : 5  (ITERATIONS=5)",
            "  margin 15%   : 6 producer iterations",
            "  producer VUS  : 1  (~6s of 270s budget)",
        ])

    def test_summary_lines_without_preset_show_the_shape(self) -> None:
        lines = summary_lines(self.size({"VUS": "5", "DURATION": "5m"}))
        self.assertEqual(lines[0], "[punch] sizing producer for consumer (VUS=5 DURATION=5m)")
        self.assertEqual(lines[3], "  producer VUS  : 4  (~216s of 270s budget)")

    def test_fractional_margin_and_budget(self) -> None:
        self.write_producer(sizing={"iterationSeconds": 1, "maxSeconds": 90.5})
        self.write_consumer(sizing={"margin": 0.125})
        lines = summary_lines(self.size({"ITERATIONS": "8"}))
        self.assertEqual(lines[2], "  margin 12.5%   : 9 producer iterations")
        self.assertEqual(lines[3], "  producer VUS  : 1  (~9s of 90.5s budget)")

    def test_budget_warning_when_one_iteration_exceeds_it(self) -> None:
        self.assertEqual(len(summary_lines(self.size({"ITERATIONS": "5"}))), 4)
        self.write_producer(sizing={"iterationSeconds": 600, "maxSeconds": 270})
        self.assertEqual(
            summary_lines(self.size({"ITERATIONS": "5"}))[-1],
            "[punch] warning: one producer iteration exceeds its 270s budget",
        )

    def test_input_warnings_name_small_inputs(self) -> None:
        plan = self.size({"ITERATIONS": "5"})  # 6 producer iterations
        self.assertEqual(input_warnings(plan, {"carts": 5, "users": 6, "x": 1}), [
            '[punch] warning: producer reads "carts" (5 rows) but runs 6 iterations; rows repeat',
            '[punch] warning: producer reads "x" (1 row) but runs 6 iterations; rows repeat',
        ])

    def test_shortfall_compares_against_rows_needed(self) -> None:
        plan = self.size({"ITERATIONS": "5"})
        self.assertEqual(shortfall(plan, {"carts": 4}),
                         ['[punch] warning: "carts" has 4 rows; consumer needs 5'])
        self.assertEqual(shortfall(plan, {"carts": 5}), [])
        self.assertEqual(shortfall(plan, {}),
                         ['[punch] warning: "carts" has 0 rows; consumer needs 5'])

    def test_next_hint_names_target_and_shape(self) -> None:
        self.assertEqual(
            next_hint(self.size({"ITERATIONS": "5"}, preset="5-iterations"), {"carts": 6}),
            "[punch] carts ready (6 rows); run consumer next with 5-iterations.",
        )
        self.assertEqual(
            next_hint(self.size({"ITERATIONS": "5"}), {"carts": 1}),
            "[punch] carts ready (1 row); run consumer next with ITERATIONS=5.",
        )

    def test_sizing_evidence(self) -> None:
        plan = self.size({"ITERATIONS": "5"}, preset="5-iterations")
        self.assertEqual(sizing_evidence(plan, {"carts": 4}), {
            "target": "consumer",
            "datasets": ["carts"],
            "shape": {"ITERATIONS": "5"},
            "preset": "5-iterations",
            "rowsNeeded": 5,
            "margin": 0.15,
            "producerIterations": 6,
            "producerVus": 1,
            "producedRows": {"carts": 4},
            "short": True,
        })
        self.assertFalse(sizing_evidence(plan, {"carts": 6})["short"])
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_sizing -v`
Expected: ImportError (`cannot import name 'input_warnings'`).

- [ ] **Step 3: Implement** — append to `src/punch/sizing.py`:

```python
def _rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


def _decimal(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(float(value))


def _shape_text(plan: SizingPlan) -> str:
    return " ".join(f"{name}={value}" for name, value in plan.shape.items())


def summary_lines(plan: SizingPlan) -> list[str]:
    assert plan.producer.sizing is not None and plan.producer.sizing.max_seconds is not None
    budget = plan.producer.sizing.max_seconds
    budget_text = _decimal(_exact(budget))
    shape = _shape_text(plan)
    lines = [
        f"[punch] sizing {plan.producer.name} for {plan.target.name} ({plan.preset or shape})",
        f"  rows needed   : {plan.rows_needed}  ({shape})",
        f"  margin {_decimal(_exact(plan.margin) * 100)}%   : "
        f"{plan.iterations} producer iterations",
        f"  producer VUS  : {plan.vus}  "
        f"(~{math.ceil(plan.estimated_seconds)}s of {budget_text}s budget)",
    ]
    if plan.estimated_seconds > budget:
        lines.append(
            f"[punch] warning: one {plan.producer.name} iteration exceeds its "
            f"{budget_text}s budget"
        )
    return lines


def input_warnings(plan: SizingPlan, row_counts: Mapping[str, int]) -> list[str]:
    """One warning per dataset the producer reads with fewer rows than its iterations."""
    return [
        f'[punch] warning: {plan.producer.name} reads "{dataset}" ({_rows(rows)}) '
        f"but runs {plan.iterations} iterations; rows repeat"
        for dataset, rows in row_counts.items()
        if rows < plan.iterations
    ]


def shortfall(plan: SizingPlan, produced: Mapping[str, int]) -> list[str]:
    return [
        f'[punch] warning: "{dataset}" has {_rows(produced.get(dataset, 0))}; '
        f"{plan.target.name} needs {plan.rows_needed}"
        for dataset in plan.datasets
        if produced.get(dataset, 0) < plan.rows_needed
    ]


def next_hint(plan: SizingPlan, produced: Mapping[str, int]) -> str:
    ready = ", ".join(
        f"{dataset} ready ({_rows(produced.get(dataset, 0))})" for dataset in plan.datasets
    )
    return f"[punch] {ready}; run {plan.target.name} next with {plan.preset or _shape_text(plan)}."


def sizing_evidence(plan: SizingPlan, produced: Mapping[str, int]) -> dict:
    rows = {dataset: produced.get(dataset, 0) for dataset in plan.datasets}
    return {
        "target": plan.target.name,
        "datasets": list(plan.datasets),
        "shape": dict(plan.shape),
        "preset": plan.preset,
        "rowsNeeded": plan.rows_needed,
        "margin": plan.margin,
        "producerIterations": plan.iterations,
        "producerVus": plan.vus,
        "producedRows": rows,
        "short": any(count < plan.rows_needed for count in rows.values()),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_sizing -v` → all OK. Full suite → OK.

- [ ] **Step 5: Commit**

```bash
git add src/punch/sizing.py tests/test_sizing.py
git commit -m "feat(sizing): summary, warnings, hint, and evidence for sized runs"
```

---

### Task 4: Menu — options mode, target and preset pickers, switch parity

**Files:**
- Modify: `src/punch/menu.py`
- Modify: `tests/test_menu.py`

**Interfaces:**
- Consumes: `sizing_pairs`, `size_producer`, `SizingError`, `SizingPlan`, `producer_environment` (Task 2); `summary_lines`, `input_warnings`, `shortfall`, `next_hint` (Task 3); existing `data_row_count`, `used_data_paths` (`punch.execution`).
- Produces: menu titles/labels other tasks' docs quote — `"Load options:"` with entries `"Options as usual"` / `"Size for a target workflow"`; `"Size for which target?"`; `"Load options for <T>:"`; `"<preset>  (not estimable: <reason>)"`; `'[punch] sizing for <T> ("<D>")'`; `'[punch] writing "<D>" (sized for <T>)'`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_menu.py`:

(a) Add a `sizing` parameter to `MenuTests.write_workflow` — signature gains
`sizing: dict | None = None,` after `description`, and right before
`path = self.root / f"{name}.yaml"` add:

```python
        if sizing is not None:
            data += (
                "  sizing:\n" + "".join(f"    {key}: {value}\n" for key, value in sizing.items())
                if sizing
                else "  sizing: {}\n"
            )
```

(b) Replace `test_switch_picks_producer_before_base_url_and_forces_produce` with:

```python
    def test_switch_asks_to_write_like_a_direct_pick(self) -> None:
        self.write_workflow("a-producer", produces={"orders": ["consumer"]})
        self.write_workflow("consumer", requires=["orders"])
        self.write_workflow("producer", forward=["BASE_URL"],
                            produces={"orders": ["consumer"]}, recommended=["orders"])
        output = io.StringIO()
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[DATA orders] 1"}):
            # top-level, workflow (consumer=1), producer picker (producer=1), base URL (0)
            with self.record_menus(0, 1, 1, 0) as calls, patch("sys.stdout", output):
                with patch("builtins.input", return_value="y") as prompt:
                    rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 4)
        entries, kwargs = calls[2]
        self.assertEqual(entries, ["a-producer", "producer  (recommended)"])
        self.assertEqual(kwargs["cursor_index"], 1)
        self.assertEqual(calls[3][1]["title"], "Pick a target:")
        self.assertIn('Write "orders" data for consumer?', prompt.call_args_list[0].args[0])
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/producer.js", call)
        self.assertEqual(
            (self.root / "data" / "orders.csv").read_text(encoding="utf-8"), "id\n1\n"
        )
        text = output.getvalue()
        self.assertNotIn("[punch] writing", text)
        self.assertIn("[punch] orders ready; run consumer next.", text)

    def test_declined_switch_write_prints_no_ready_hint(self) -> None:
        self.write_pair()
        output = io.StringIO()
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[DATA orders] 1"}):
            # top-level, workflow (consumer=0), producer picker (0)
            with self.record_menus(0, 0, 0), patch("sys.stdout", output):
                with patch("builtins.input", return_value="n"):
                    rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake_docker_calls()), 1)
        self.assertFalse((self.root / "data" / "orders.csv").exists())
        self.assertNotIn("ready", output.getvalue())
```

(c) Add the sizing tests to `MenuTests`:

```python
    SIZED_PRODUCER = {"iterationSeconds": 1, "maxSeconds": 270}

    def write_sized_pair(self, *, presets: bool = True) -> None:
        self.write_workflow(
            "producer", forward=["BASE_URL", "VUS", "DURATION", "ITERATIONS"],
            produces={"orders": ["consumer"]}, sizing=self.SIZED_PRODUCER,
        )
        self.write_workflow(
            "consumer", forward=["BASE_URL", "VUS", "ITERATIONS"],
            requires=["orders"], sizing={"margin": 0.15},
        )
        if presets:
            self.write_options("5-iterations", {"ITERATIONS": 5})
            self.write_options("5-vu-5m", {"VUS": 5, "DURATION": "5m"})

    @staticmethod
    def orders_stdout(count: int) -> str:
        return "|".join(f"[DATA orders] {index}" for index in range(1, count + 1))

    def run_sized(self, *indices: int | None, rows: int = 6):
        output = io.StringIO()
        environment = {"FAKE_DOCKER_STDOUT": self.orders_stdout(rows), "DURATION": "5m"}
        with patch.dict(os.environ, environment):
            with self.record_menus(*indices) as calls, patch("sys.stdout", output):
                with patch("builtins.input", return_value="n") as prompt:
                    rc = run_menu(self.root, options_dir=self.root_options_dir())
        return rc, calls, output.getvalue(), prompt

    def test_sized_run_overrides_shape_and_writes_without_asking(self) -> None:
        self.write_sized_pair()
        # top-level, workflow (producer=1), base URL (0), mode (size=1), preset (5-iterations=0)
        rc, calls, text, prompt = self.run_sized(0, 1, 0, 1, 0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[3][0], ["Options as usual", "Size for a target workflow"])
        self.assertEqual(calls[3][1]["title"], "Load options:")
        self.assertEqual(
            calls[4][0],
            ["5-iterations", "5-vu-5m  (not estimable: consumer does not forward DURATION)"],
        )
        self.assertEqual(calls[4][1]["title"], "Load options for consumer:")
        self.assertEqual(calls[4][1]["cursor_index"], 0)
        prompt.assert_not_called()
        [call] = self.fake_docker_calls()
        self.assertIn("ITERATIONS=6", call)
        self.assertIn("VUS=1", call)
        self.assertFalse(any(part.startswith("DURATION=") for part in call))
        self.assertEqual(
            (self.root / "data" / "orders.csv").read_text(encoding="utf-8"),
            "id\n1\n2\n3\n4\n5\n6\n",
        )
        for line in (
            '[punch] sizing for consumer ("orders")',
            "[punch] sizing producer for consumer (5-iterations)",
            "  rows needed   : 5  (ITERATIONS=5)",
            "  margin 15%   : 6 producer iterations",
            "  producer VUS  : 1  (~6s of 270s budget)",
            '[punch] writing "orders" (sized for consumer)',
            "[punch] orders ready (6 rows); run consumer next with 5-iterations.",
        ):
            self.assertIn(line, text)
        self.assertNotIn("warning", text)

    def test_not_estimable_preset_re_asks(self) -> None:
        self.write_sized_pair()
        rc, calls, _, _ = self.run_sized(0, 1, 0, 1, 1, 0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[5][1]["title"], "Load options for consumer:")
        self.assertEqual(calls[5][1]["cursor_index"], 1)
        [call] = self.fake_docker_calls()
        self.assertIn("ITERATIONS=6", call)

    def test_esc_on_target_preset_cancels_before_docker(self) -> None:
        self.write_sized_pair()
        rc, _, text, _ = self.run_sized(0, 1, 0, 1, None)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn("[punch] menu canceled.", text)

    def test_shortfall_warns_and_keeps_exit_code(self) -> None:
        self.write_sized_pair()
        rc, _, text, _ = self.run_sized(0, 1, 0, 1, 0, rows=3)
        self.assertEqual(rc, 0)
        self.assertIn('[punch] warning: "orders" has 3 rows; consumer needs 5', text)
        self.assertIn("[punch] orders ready (3 rows); run consumer next with 5-iterations.", text)

    def test_options_as_usual_keeps_preset_and_produce_prompt(self) -> None:
        self.write_sized_pair()
        # mode (usual=0), options preset picker ("5-iterations" = 1 after "Skip")
        rc, calls, text, prompt = self.run_sized(0, 1, 0, 0, 1)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[4][0], ["Skip (use env/default)", "5-iterations", "5-vu-5m"])
        self.assertIn('Write "orders" data for consumer?', prompt.call_args_list[0].args[0])
        [call] = self.fake_docker_calls()
        self.assertIn("ITERATIONS=5", call)
        self.assertNotIn("[punch] sizing", text)

    def test_no_presets_means_no_mode_picker(self) -> None:
        self.write_sized_pair(presets=False)
        rc, calls, _, prompt = self.run_sized(0, 1, 0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        prompt.assert_called_once()

    def test_switched_producer_gets_the_same_mode_picker(self) -> None:
        self.write_sized_pair()
        # top-level, workflow (consumer=0), producer picker (0), base URL (0), mode (1), preset (0)
        rc, calls, text, prompt = self.run_sized(0, 0, 0, 0, 1, 0)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[4][0], ["Options as usual", "Size for a target workflow"])
        prompt.assert_not_called()
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/producer.js", call)
        self.assertIn("ITERATIONS=6", call)
        self.assertIn("[punch] orders ready (6 rows); run consumer next with 5-iterations.", text)
        self.assertNotIn("[punch] orders ready; run consumer next.", text)

    def test_target_picker_lists_each_sized_target(self) -> None:
        self.write_sized_pair()
        self.write_workflow(
            "producer", forward=["BASE_URL", "VUS", "DURATION", "ITERATIONS"],
            produces={"orders": ["consumer", "other"]}, sizing=self.SIZED_PRODUCER,
        )
        self.write_workflow(
            "other", forward=["BASE_URL", "VUS", "ITERATIONS"],
            requires=["orders"], sizing={},
        )
        # workflows sorted: consumer, other, producer=2; target picker other=1
        rc, calls, text, _ = self.run_sized(0, 2, 0, 1, 1, 0, rows=5)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[4][0], ['"orders" for consumer', '"orders" for other'])
        self.assertEqual(calls[4][1]["title"], "Size for which target?")
        [call] = self.fake_docker_calls()
        self.assertIn("ITERATIONS=5", call)  # other: margin 0
        self.assertIn("[punch] orders ready (5 rows); run other next with 5-iterations.", text)
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_menu -v`
Expected: the new sizing tests fail (no mode picker: menu call counts and
`ITERATIONS=6` differ); `test_switch_asks_to_write_like_a_direct_pick` fails
(`prompt` never called — forced write still in place);
`test_declined_switch_write_prints_no_ready_hint` fails (data written).

- [ ] **Step 3: Implement** — `src/punch/menu.py`:

Imports: add `data_row_count` to the `from punch.execution import (...)` list, and add:

```python
from punch.sizing import (
    SizingError,
    SizingPlan,
    input_warnings,
    next_hint,
    producer_environment,
    shortfall,
    size_producer,
    sizing_pairs,
    summary_lines,
)
```

Replace `_choose_produce` with:

```python
def _choose_produce(workflow: K6Workflow, sized: Optional[SizingPlan] = None) -> tuple[str, ...]:
    if workflow.data is None:
        return ()
    chosen = []
    for product in workflow.data.produces:
        if sized is not None and product.dataset in sized.datasets:
            print(f'[punch] writing "{product.dataset}" (sized for {sized.target.name})')
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

Add after `_choose_options`:

```python
def _choose_target_preset(
    workflow: K6Workflow, target: str, catalog, paths: List[Path]
) -> SizingPlan:
    entries: List[str] = []
    plans: List[Optional[SizingPlan]] = []
    for path in paths:
        try:
            plan = size_producer(
                workflow, target, catalog, _load_options_preset(path), preset=path.stem
            )
        except SizingError as error:
            entries.append(f"{path.stem}  (not estimable: {error})")
            plans.append(None)
        else:
            entries.append(path.stem)
            plans.append(plan)
    cursor = next(
        (index for index, path in enumerate(paths) if path.stem == _DEFAULT_OPTIONS_PRESET), 0
    )
    while True:
        cursor = _select(entries, f"Load options for {target}:", cursor_index=cursor)
        plan = plans[cursor]
        if plan is not None:
            return plan


def _choose_sizing(
    workflow: K6Workflow, catalog, options_dir: Path, optional_choices
) -> Optional[SizingPlan]:
    """Options mode for a producer with sized targets; None keeps today's options flow."""
    pairs = sizing_pairs(workflow, catalog)
    paths = discover_options(options_dir)
    if not pairs or not paths:
        return None
    if _select(["Options as usual", "Size for a target workflow"], "Load options:") == 0:
        return None
    datasets_by_target: dict = {}
    for dataset, target in pairs:
        datasets_by_target.setdefault(target, []).append(dataset)
    quoted = {
        target: ", ".join(f'"{dataset}"' for dataset in datasets)
        for target, datasets in datasets_by_target.items()
    }
    targets = list(datasets_by_target)
    if len(targets) == 1:
        target = targets[0]
        print(f"[punch] sizing for {target} ({quoted[target]})")
    else:
        target = targets[
            _select([f"{quoted[name]} for {name}" for name in targets], "Size for which target?")
        ]
    plan = _choose_target_preset(workflow, target, catalog, paths)
    for line in summary_lines(plan):
        print(line)
    row_counts = {
        dataset: data_row_count(path)
        for dataset, path in used_data_paths(workflow, {}, optional_choices).items()
    }
    for line in input_warnings(plan, row_counts):
        print(line)
    return plan
```

In `_run_workflow_menu`, replace

```python
    base_url = _choose_base_url(workflow)
    options = _choose_options(workflow, resolved_options_dir)
    produce = _choose_produce(workflow, plan.produce)

    environment = dict(os.environ)
    if base_url is not None:
        environment["BASE_URL"] = base_url
    environment.update(options)
```

with

```python
    base_url = _choose_base_url(workflow)
    sized = _choose_sizing(workflow, catalog, resolved_options_dir, choices)
    options = _choose_options(workflow, resolved_options_dir) if sized is None else {}
    produce = _choose_produce(workflow, sized)

    environment = dict(os.environ)
    if base_url is not None:
        environment["BASE_URL"] = base_url
    environment.update(options)
    if sized is not None:
        environment = producer_environment(environment, sized)
```

and replace

```python
    if result.passed:
        _print_metrics(workflow)
        if plan.switched_from:
            print(switch_hint(plan, catalog))
```

with

```python
    if result.passed:
        _print_metrics(workflow)
        if sized is not None:
            produced = {dataset.dataset: dataset.record_count for dataset in result.datasets}
            for line in shortfall(sized, produced):
                print(line)
            print(next_hint(sized, produced))
        elif plan.switched_from and plan.produce[0] in produce:
            print(switch_hint(plan, catalog))
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_menu -v` → all OK (every pre-existing menu
test still passes: their workflows declare no `spec.sizing`, so no mode
picker appears). Full suite → OK.

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py tests/test_menu.py
git commit -m "feat(menu): size a producer for a target; ask before a switched write"
```

---

### Task 5: `punch run --size-for`

**Files:**
- Modify: `src/punch/__main__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `size_producer`, `SizingError`, `producer_environment` (Task 2); `summary_lines`, `input_warnings`, `shortfall`, `next_hint`, `sizing_evidence` (Task 3).
- Produces: CLI flag `--size-for TARGET`; evidence per-result key `"sizing"` (shape of `sizing_evidence`); switch note `[punch] ignoring <flags joined by "/"> for <W>`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_cli.py`:

(a) In `test_switch_drops_consumer_flags_with_note`, change the expected note to
`"[punch] ignoring --data for data-consumer"`. Then confirm nothing else
quotes the old text:
`grep -rn "ignoring --produce/--data\|--produce and --data apply" src tests docs README.md`
→ only `src/punch/__main__.py` lines remain (changed in Step 3).

(b) Add to `CliTests`:

```python
    SHAPE = "VUS, DURATION, ITERATIONS"

    def make_sizable(
        self, target_sizing: str = "  sizing:\n    iterationSeconds: 2\n    margin: 0.15\n"
    ) -> None:
        producer = self.producer_path.read_text(encoding="utf-8")
        self.producer_path.write_text(
            producer.replace("forward: [BASE_URL, RUN_ID]", f"forward: [BASE_URL, RUN_ID, {self.SHAPE}]")
            + "  sizing:\n    iterationSeconds: 1\n    maxSeconds: 270\n",
            encoding="utf-8",
        )
        consumer = self.consumer_path.read_text(encoding="utf-8")
        self.consumer_path.write_text(
            consumer.replace("forward: [BASE_URL]", f"forward: [BASE_URL, {self.SHAPE}]")
            + target_sizing,
            encoding="utf-8",
        )

    def run_size_for(self, target: str = "data-consumer", **environment: str):
        os.environ.update({"RUN_ID": "run-1", **environment})
        output = io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", output):
            rc = main(["run", str(self.producer_path), "--size-for", target])
        return rc, output.getvalue()

    def assertSizingStop(self, rc: int, reason: str) -> None:
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn(reason, result["failure"])
        self.assertNotIn("sizing", result)

    def test_size_for_overrides_shape_and_writes_dataset(self) -> None:
        self.make_sizable()
        rows = "|".join(f"[DATA carts] c{index},p,s" for index in range(5))
        rc, text = self.run_size_for(
            ITERATIONS="4", VUS="2", DURATION="1m", FAKE_DOCKER_STDOUT=rows
        )
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("ITERATIONS=5", call)
        self.assertIn("VUS=1", call)
        self.assertFalse(any(argument.startswith("DURATION=") for argument in call))
        self.assertEqual(len(self.carts_path.read_text(encoding="utf-8").splitlines()), 6)
        self.assertEqual(self.evidence()["results"][0]["sizing"], {
            "target": "data-consumer",
            "datasets": ["carts"],
            "shape": {"ITERATIONS": "4", "VUS": "2", "DURATION": "1m"},
            "preset": None,
            "rowsNeeded": 4,
            "margin": 0.15,
            "producerIterations": 5,
            "producerVus": 1,
            "producedRows": {"carts": 5},
            "short": False,
        })
        self.assertIn(
            "[punch] sizing data-producer for data-consumer (ITERATIONS=4 VUS=2 DURATION=1m)", text
        )
        self.assertIn(
            "[punch] carts ready (5 rows); run data-consumer next with "
            "ITERATIONS=4 VUS=2 DURATION=1m.",
            text,
        )

    def test_size_for_duration_shape_and_shortfall(self) -> None:
        self.make_sizable()
        rc, text = self.run_size_for(VUS="5", DURATION="5m", FAKE_DOCKER_STDOUT="[DATA carts] c,p,s")
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("ITERATIONS=863", call)
        self.assertIn("VUS=4", call)
        self.assertIn("  producer VUS  : 4  (~216s of 270s budget)", text)
        self.assertIn('[punch] warning: "carts" has 1 row; data-consumer needs 750', text)
        sizing = self.evidence()["results"][0]["sizing"]
        self.assertEqual((sizing["producedRows"], sizing["short"]), ({"carts": 1}, True))

    def test_size_for_explicit_produce_is_not_duplicated(self) -> None:
        self.make_sizable()
        os.environ.update({"RUN_ID": "run-1", "ITERATIONS": "1", "FAKE_DOCKER_STDOUT": "[DATA carts] c,p,s"})
        with patch("sys.stdout", io.StringIO()):
            rc = main(["run", str(self.producer_path), "--produce", "carts", "--size-for", "data-consumer"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            [dataset["dataset"] for dataset in self.evidence()["results"][0]["datasets"]], ["carts"]
        )

    def test_size_for_non_sizable_producer_fails_before_docker(self) -> None:
        rc, _ = self.run_size_for(ITERATIONS="5")
        self.assertSizingStop(rc, "data-producer is not a sizable producer")

    def test_size_for_unknown_target_fails_before_docker(self) -> None:
        self.make_sizable()
        rc, _ = self.run_size_for("nope", ITERATIONS="5")
        self.assertSizingStop(rc, "data-producer does not produce data for nope")

    def test_size_for_unsized_target_fails_before_docker(self) -> None:
        self.make_sizable(target_sizing="")
        rc, _ = self.run_size_for(ITERATIONS="5")
        self.assertSizingStop(rc, "data-consumer declares no spec.sizing")

    def test_size_for_non_estimable_shape_fails_before_docker(self) -> None:
        self.make_sizable()
        rc, _ = self.run_size_for()
        self.assertSizingStop(rc, "shape sets neither ITERATIONS nor DURATION")

    def test_size_for_is_rejected_with_all(self) -> None:
        self.assertEqual(main(["run", "all", "--size-for", "data-consumer"]), 1)
        self.assertEqual(self.compose_run_count(), 0)

    def test_switch_drops_size_for_with_note(self) -> None:
        os.environ.update({"RUN_ID": "run-1", "FAKE_DOCKER_STDOUT": "[DATA carts] c,p,s"})
        with self.picker(0) as (_, output):
            rc = main(["run", str(self.consumer_path), "--size-for", "data-status"])
        self.assertEqual(rc, 0)
        self.assertIn("[punch] ignoring --size-for for data-consumer", output.getvalue())
        self.assertNotIn("sizing", self.evidence()["results"][0])
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"), "cartId,productId,sid\nc,p,s\n"
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_cli -v`
Expected: argparse `unrecognized arguments: --size-for` (SystemExit 2) in the
new tests; `test_switch_drops_consumer_flags_with_note` fails on the note text.

- [ ] **Step 3: Implement** — `src/punch/__main__.py`:

(a) `build_parser`, after the `--no-input` argument:

```python
    run_p.add_argument("--size-for", metavar="TARGET", default=None,
                       help="Size this producer run for TARGET: rows from TARGET's "
                            "ITERATIONS/VUS/DURATION in the environment plus its margin; "
                            "implies --produce for the datasets it feeds TARGET.")
```

(b) `_evidence_result` gains a keyword parameter `sizing: dict | None = None,`
(after `data_sources`) and one more entry at the end of the returned dict:

```python
        **({"sizing": sizing} if sizing else {}),
```

(c) In `cmd_run`, extend the local `from punch.execution import (...)` with
`data_row_count`, and add:

```python
    from punch.sizing import (
        SizingError,
        input_warnings,
        next_hint,
        producer_environment,
        shortfall,
        size_producer,
        sizing_evidence,
        summary_lines,
    )
```

Replace the `all` guard:

```python
    if args.selector == "all" and (args.produce or args.data or args.size_for):
        print("[punch] --produce, --data, and --size-for apply to one selected workflow, not 'all'",
              file=sys.stderr, flush=True)
        return 1
```

Replace `produce_args, data_args = list(args.produce), list(args.data)` with:

```python
    produce_args, data_args = list(args.produce), list(args.data)
    size_for: str | None = args.size_for
```

Replace the switched-plan block

```python
                if plan.switched_from:
                    if produce_args or data_args:
                        print(f"[punch] ignoring --produce/--data for {selected.name}", flush=True)
                    produce_args, data_args = list(plan.produce), []
                    workflows = runnable = [plan.workflow]
```

with

```python
                if plan.switched_from:
                    ignored = [
                        flag for flag, given in (
                            ("--produce", produce_args),
                            ("--data", data_args),
                            ("--size-for", size_for),
                        ) if given
                    ]
                    if ignored:
                        print(f"[punch] ignoring {'/'.join(ignored)} for {selected.name}",
                              flush=True)
                    produce_args, data_args, size_for = list(plan.produce), [], None
                    workflows = runnable = [plan.workflow]
```

Right after `switched_from = plan.switched_from if plan is not None else ()` add:

```python
    sizing_plan = None
    if size_for is not None and runnable:
        sized_workflow = runnable[0]
        try:
            sizing_plan = size_producer(
                sized_workflow, size_for, load_catalog(sized_workflow.source_path.parent),
                os.environ,
            )
        except (CatalogError, SizingError) as error:
            print(f"[punch] {error}", file=sys.stderr, flush=True)
            results.append(_evidence_result(
                sized_workflow,
                ExecutionResult(sized_workflow.name, (), None, False, str(error)),
            ))
            runnable, stopped_rc = [], 1
        else:
            produce_args = list(dict.fromkeys([*produce_args, *sizing_plan.datasets]))
```

In the per-workflow loop, replace from `choices = plan.optional_choices if plan is not None else preset`
through the `if result.passed and switched_from:` block with:

```python
        choices = plan.optional_choices if plan is not None else preset
        environment = os.environ
        if sizing_plan is not None:
            environment = producer_environment(os.environ, sizing_plan)
            for line in summary_lines(sizing_plan):
                print(line, flush=True)
            row_counts = {
                dataset: data_row_count(path)
                for dataset, path in used_data_paths(workflow, overrides, choices).items()
            }
            for line in input_warnings(sizing_plan, row_counts):
                print(line, flush=True)

        result = execute_workflow(
            workflow,
            environment=environment,
            produce=produce,
            data_overrides=overrides,
            optional_choices=choices,
            producers_of=catalog.producers_of,
            log_path=LOGS_DIR / f"k6-{workflow.name}.log",
        )
        produced = {dataset.dataset: dataset.record_count for dataset in result.datasets}
        results.append(_evidence_result(
            workflow,
            result,
            switched_from=switched_from,
            data_sources=data_sources(workflow, overrides, choices),
            sizing=sizing_evidence(sizing_plan, produced) if sizing_plan is not None else None,
        ))
        if result.passed and switched_from:
            print(switch_hint(plan, catalog), flush=True)
        if result.passed and sizing_plan is not None:
            for line in shortfall(sizing_plan, produced):
                print(line, flush=True)
            print(next_hint(sizing_plan, produced), flush=True)
```

(the `if result.child_exit_code is not None: confirm_delete_consumed(...)` block and
everything after it stay as they are).

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_cli -v` → all OK. Full suite → OK.
Run: `grep -nE '"(http|browser)-|carts|orders' src/punch/sizing.py src/punch/menu.py src/punch/__main__.py`
→ no new workflow/dataset literal (pre-existing `orders-api` service log names in `__main__.py` are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/punch/__main__.py tests/test_cli.py
git commit -m "feat(run): --size-for sizes a producer run for a target"
```

---

### Task 6: Punch docs

**Files:**
- Modify: `README.md`, `docs/workflows/validation.md`, `CLAUDE.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: flag, YAML keys, picker labels, evidence key from Tasks 1–5.

- [ ] **Step 1: `README.md`** —
  - In the command block near the top, after `./bin/punch run path/to/consumer.yaml --data orders=data/batch-2.csv` add
    `./bin/punch run path/to/producer.yaml --size-for consumer   # size it for consumer's ITERATIONS/VUS/DURATION`.
  - In the `spec:` YAML example, insert before `  data:`:

    ```yaml
      sizing:                    # optional; see "Sizing for a target"
        iterationSeconds: 1.0    # one iteration on one VU
        maxSeconds: 270          # budget when sized as a producer
        margin: 0.15             # extra rows when sized for as a target
    ```
  - In the pickers bullet, replace
    `Picking one runs it with \`--produce <dataset>\` — still one Compose run —`
    with
    `` `punch run` runs the picked one with `--produce <dataset>`; the menu continues exactly as if it had been picked directly (it asks before writing) — still one Compose run — ``.
  - Add a bullet after the pickers bullet:

    ```markdown
    - **Sizing for a target.** A producer that declares `spec.sizing`
      `iterationSeconds` + `maxSeconds` and forwards `ITERATIONS` and `VUS` can
      be sized for any `produces[].targets` workflow that declares
      `spec.sizing`. Punch reads the target's load shape — `ITERATIONS`, or
      `VUS` + `DURATION` with the target's `iterationSeconds`, only from names
      the target forwards — adds the target's `margin`, and runs the producer
      with `ITERATIONS=⌈rows × (1 + margin)⌉`, just enough `VUS` to finish
      inside `maxSeconds`, and no `DURATION`. The menu offers
      `Options as usual` / `Size for a target workflow` (when `options/` has
      presets) and asks for the target's preset; `--size-for <target>` reads
      the shape from the environment. Fewer produced rows than the target needs
      prints a warning; the exit code is unchanged.
    ```

- [ ] **Step 2: `docs/workflows/validation.md`** —
  - Under "Optional per-result keys" add:

    ```markdown
    - `sizing` — present when `--size-for` sized the run: `target`, `datasets`,
      `shape` (the target's `ITERATIONS`/`VUS`/`DURATION` used), `preset`
      (`null` on the CLI), `rowsNeeded`, `margin`, `producerIterations`,
      `producerVus`, `producedRows` (`{"<dataset>": <count>}`), and `short`
      (`true` when a sized dataset has fewer rows than `rowsNeeded`).
    ```
  - In the host-setup paragraph (`Datasets are optional and declared in…`;
    the phrases below wrap across lines — match them ignoring line breaks and
    reflow the paragraph to ~80 columns afterwards), replace
    `The picked producer runs with \`--produce <dataset>\` — still one Compose run —`
    with
    `` `punch run` runs the picked producer with `--produce <dataset>` and the menu continues as for a direct pick (asking before writing) — still one Compose run — ``,
    and insert after the sentence ending `…or running the producer with \`--produce\`.`:
    `` A producer can instead be sized for a target with `spec.sizing` — the menu's `Size for a target workflow` mode or `punch run <producer> --size-for <target>` — which prints the estimate, writes the sized datasets, and warns when fewer rows came out than the target needs. ``

- [ ] **Step 3: `CLAUDE.md`** —
  - Project structure: after the `data_plan.py` line add
    `    │       ├── sizing.py                 # size a producer run for a target's rows (stdlib-only)`.
  - "For AI assistants" `src/punch/` bullet: after `` `--no-input` skips the pickers. `` add
    `` `src/punch/sizing.py` sizes a producer for a target from `spec.sizing` (menu options mode, `--size-for`). ``

- [ ] **Step 4: `CHANGELOG.md`** — first entry under `## [Unreleased]`:

```markdown
- Target data sizing: `spec.sizing` (`iterationSeconds`, `maxSeconds`,
  `margin`), the menu's `Size for a target workflow` mode, and
  `punch run --size-for <target>` size a producer run for the rows its target
  reads. After a producer switch the menu now asks before writing the
  switched dataset.
```

- [ ] **Step 5: Check and commit**

Run: `grep -rn "reserved\|unused" README.md docs/workflows/validation.md CLAUDE.md | grep -i target` → no stale "targets unused/reserved" wording (fix any hit).
Run: full suite → OK.

```bash
git add README.md docs/workflows/validation.md CLAUDE.md CHANGELOG.md
git commit -m "docs: target data sizing"
```

---

### Task 7: Parent repository — measured sizing, docs, integration

Runs from the parent repo root
(`/Users/nicolaspizarro/repo/expresso-engineering-playground`), on a new
branch: `git switch -c feat/target-data-sizing`.

**Files:**
- Modify: `tests/performance/k6/workflows/{http-cart,browser-cart,http-purchase,browser-purchase,http-purchase-registered,http-orders,http-auth-login,http-orders-status,http-me-hot-status}.yaml`
- Modify: `tests/performance/k6/README.md`, `docs/performance/orchestrator.md`, `docs/next-steps/README.md`
- Create: `docs/next-steps/sizing-duration-targets.md`
- Modify: `vendor/punch` submodule pointer

**Interfaces:**
- Consumes: `spec.sizing` schema (Task 1), `--size-for` (Task 5), menu mode (Task 4).

- [ ] **Step 1: Bring up the stack**

```bash
./dev up web          # postgres + bff + web (browser workflows target the web app)
docker compose -f infra/docker/compose.performance.yaml build k6 k6-browser
PUNCH="env PYTHONPATH=vendor/punch/src /tmp/punch-venv/bin/python -m punch run --no-input"
WF=tests/performance/k6/workflows
```

- [ ] **Step 2: Measure one-VU pace (10 iterations each), producers before consumers**

```bash
export VUS=1 ITERATIONS=10
$PUNCH $WF/http-cart.yaml --produce carts
$PUNCH $WF/http-orders.yaml --produce orders
$PUNCH $WF/http-orders-status.yaml
$PUNCH $WF/http-purchase.yaml --produce orders
$PUNCH $WF/http-purchase-registered.yaml --produce owned-orders
$PUNCH $WF/http-auth-login.yaml --produce auth-tokens
$PUNCH $WF/http-me-hot-status.yaml
$PUNCH $WF/browser-cart.yaml
$PUNCH $WF/browser-purchase.yaml
unset VUS ITERATIONS
```

If one fails (missing `DEMO_PASSWORD`, stack down), fix the cause and re-run
that line; do not guess a pace. Then:

```bash
python3 - <<'EOF'
import json, math, pathlib
names = ["http-cart", "browser-cart", "http-purchase", "browser-purchase",
         "http-purchase-registered", "http-orders", "http-auth-login",
         "http-orders-status", "http-me-hot-status"]
for name in names:
    summary = json.loads(pathlib.Path(f"tests/performance/k6/reports/{name}-summary.json").read_text())
    pace = math.ceil(summary["durationMs"] / 10 / 100) / 10  # seconds, rounded up to 0.1
    print(f"{name:26} iterationSeconds={pace:<6} errorRate={summary['errorRate']:.3f} "
          f"checkPassRate={summary['checkPassRate']:.3f}")
EOF
```

Record the table (it goes in the commit message body).

- [ ] **Step 3: Add `spec.sizing` to every workflow** — append at the end of each YAML
(after `spec.data`, two-space indent), with `<pace>` from Step 2:

| Workflow | Block |
|---|---|
| `http-cart`, `browser-cart`, `http-purchase`, `browser-purchase`, `http-purchase-registered` | `iterationSeconds: <pace>`, `maxSeconds: 270` |
| `http-orders`, `http-auth-login` | `iterationSeconds: <pace>`, `maxSeconds: 270`, `margin: 0.15` |
| `http-orders-status`, `http-me-hot-status` | `iterationSeconds: <pace>`, `margin: 0.15` |

Example (`http-cart.yaml`):

```yaml
  sizing:
    iterationSeconds: 1.1   # measured: VUS=1 ITERATIONS=10, durationMs / 10
    maxSeconds: 270         # 30 s under the scenario's 5m maxDuration
```

Raise a target's `margin` above `0.15` only if Step 2 measured its
producers' `errorRate` above `0.15`; note it in a comment if so. In
`http-orders.yaml` and `http-orders-status.yaml`, add above
`environment.forward`:

```yaml
    # TODO(next-steps/sizing-duration-targets): forward DURATION once the scenario runs time-based
```

Verify the catalog:

```bash
PYTHONPATH=vendor/punch/src /tmp/punch-venv/bin/python - <<'EOF'
from punch.catalog import load_catalog
from punch.sizing import sizing_pairs
catalog = load_catalog("tests/performance/k6/workflows")
for name, workflow in sorted(catalog.workflows.items()):
    print(name, workflow.sizing, sizing_pairs(workflow, catalog))
EOF
```

Expected: all nine load; pairs `http-cart`/`browser-cart` → `("carts", "http-orders")`;
`http-purchase`/`browser-purchase`/`http-orders` → `("orders", "http-orders-status")`;
`http-auth-login` → `("auth-tokens", "http-me-hot-status")`;
`http-purchase-registered` → `("owned-orders", "http-auth-login")`.

- [ ] **Step 4: Live sized run (the spec's evidence run)**

```bash
ITERATIONS=5 $PUNCH $WF/http-cart.yaml --size-for http-orders
```

Expected output contains `rows needed   : 5  (ITERATIONS=5)`,
`margin 15%   : 6 producer iterations`, `carts ready (6 rows); run http-orders next with ITERATIONS=5.`,
and no `warning`. Check evidence:
`python3 -c "import json;print(json.load(open('vendor/punch/reports/state/punch-run.json'))['results'][0]['sizing'])"`
→ `"short": False`, `"producedRows": {"carts": 6}`. Then the target run:
`ITERATIONS=5 $PUNCH $WF/http-orders.yaml` → passes.
Then one interactive check: `PYTHONPATH=vendor/punch/src /tmp/punch-venv/bin/python -m punch menu tests/performance/k6/workflows`
→ pick `http-cart` → `Size for a target workflow` → `5-iterations`; summary shows 6 producer iterations.

- [ ] **Step 5: Parent docs**
  - `tests/performance/k6/README.md`: after the dataset table in "Data pipeline (produce / require)", add a subsection:

    ````markdown
    ### Size a producer for a target

    Every workflow here declares `spec.sizing`: a measured one-VU
    `iterationSeconds`, `maxSeconds: 270` on producers (30 s under the
    scenarios' 5 m `maxDuration`), and `margin: 0.15` on targets. Instead of
    picking producer options, size the producer for the run you plan next:

    ```bash
    # menu: pick http-cart → "Size for a target workflow" → pick http-orders' preset
    PYTHONPATH=vendor/punch/src python3 -m punch menu tests/performance/k6/workflows

    # CLI: the environment is the target's load shape
    ITERATIONS=50 PYTHONPATH=vendor/punch/src python3 -m punch run \
      tests/performance/k6/workflows/http-cart.yaml --size-for http-orders
    ```

    Punch prints the estimate (rows needed, producer iterations with margin,
    producer VUs and time), writes the dataset, warns when fewer rows came out
    than the target needs, and names the target run to do next. `http-orders`
    and `http-orders-status` forward `ITERATIONS` only, so `DURATION` presets
    show as not estimable for them
    ([follow-up](../../../docs/next-steps/sizing-duration-targets.md)).
    ````
  - Same file, data-pipeline consumer bullet: replace `runs the picked one with
    \`--produce\`,` (wrapped across two lines) with
    `` runs the picked one (`punch run` with `--produce`; the menu asks before writing, as for a direct pick), `` and reflow the bullet.
  - `docs/performance/orchestrator.md`: at the end of `## Data pipeline (\`spec.data\`)` (just before `## Summary output and Docker Compose confirmation`), add:

    ```markdown
    ### Sizing for a target (`spec.sizing`)

    A workflow may declare `spec.sizing` (`iterationSeconds`, `maxSeconds`,
    `margin`). Punch sizes a producer for a target in its `produces[].targets`:
    rows needed come from the target's `ITERATIONS` (or `VUS` × `DURATION` /
    `iterationSeconds`), the producer runs `⌈rows × (1 + margin)⌉` iterations
    with enough VUs to fit `maxSeconds`, still in one Compose run. Entry points:
    the menu's `Size for a target workflow` mode and `punch run --size-for`.
    Contract: `vendor/punch/docs/specs/spec-target-data-sizing.md`.
    ```
  - Create `docs/next-steps/sizing-duration-targets.md`:

    ```markdown
    # Sizing: DURATION shapes for ITERATIONS-only targets

    Status: open.
    Spec: `vendor/punch/docs/specs/spec-target-data-sizing.md`

    ## Problem

    Punch sizes a producer for a target from the target's load shape.
    `http-orders` and `http-orders-status` forward `VUS` and `ITERATIONS` only:
    their scenarios read a fixed pool, one row per iteration, with a
    `shared-iterations` executor. A `VUS` + `DURATION` preset such as
    `options/5-vu-5m.json` is therefore "not estimable" for them.

    ## Proposal

    With sizing, a time-based soak over a sized pool no longer wraps. Give both
    scenarios the same `ITERATIONS`-or-`DURATION` switch the other HTTP
    scenarios use (`constant-vus` for `DURATION`), forward `DURATION` in both
    workflow YAMLs, and drop the two `TODO(next-steps/sizing-duration-targets)`
    anchors.

    ## Done when

    `VUS=5 DURATION=5m punch run …/http-cart.yaml --size-for http-orders` sizes
    without error, and a following `VUS=5 DURATION=5m` `http-orders` run passes
    without reusing a cart.
    ```
  - `docs/next-steps/README.md`: append to "Open threads (priority order)" as the next number:
    `**[Sizing DURATION targets](sizing-duration-targets.md)** — _performance tooling follow-up_` with one sub-bullet
    `- Let http-orders / http-orders-status run time-based so DURATION presets can be sized (2 anchors).`

- [ ] **Step 6: Verify parent**

```bash
pnpm pg:test          # orchestrator unittest suite (deprecated k6runner still imports punch)
grep -rn "next-steps/sizing-duration-targets" tests/performance/k6/workflows | wc -l   # → 2
```

(Prettier's `pnpm format` only checks workspace packages; the YAML and
Markdown touched here are outside them.)

- [ ] **Step 7: Integrate Punch, push, bump pointer**

This plan's approval is the go-ahead to push Punch (memory rule: an
approved plan that says so counts as asking; an unpushed pointer breaks CI
checkout).

```bash
git -C vendor/punch switch main
git -C vendor/punch merge --ff-only feat/target-data-sizing
git -C vendor/punch push origin main
git -C vendor/punch log origin/main..HEAD --oneline   # → empty
git add vendor/punch tests/performance/k6/workflows tests/performance/k6/README.md \
  docs/performance/orchestrator.md docs/next-steps/sizing-duration-targets.md docs/next-steps/README.md
git commit -m "feat(perf): size producers for target workflows" -m "<Step 2 pace table>"
```

Then use superpowers:finishing-a-development-branch for the parent branch.

---

## Self-review notes

- Spec coverage: schema (T1); definitions, eligibility, math, exact ceilings, reasons (T2); summary, budget warning, input warnings, shortfall, hint, evidence (T3); menu FR3.1–3.8 incl. supersede (T4); CLI FR4 (T5); FR5 untouched modules (Global Constraints, T5 grep); docs (T6); parent FR6 + live evidence run (T7).
- Picker labels, flag name, evidence keys, and function names match across T2–T7.

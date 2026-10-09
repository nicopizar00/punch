"""Size a producer run for the rows a target run reads.

Standard-library only. Pace, budget, and margin come from each workflow's
`spec.sizing`; producer → target links from `spec.data.produces[].targets`.
The load shape is the k6 options JSON a run passes to `k6 run --config`:
Punch reads a target's shape from the config it would run with, and runs a
sized producer with a copy of the producer's own `spec.k6.config` whose
execution becomes `shared-iterations` with the sized VUs and iterations.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from punch.catalog import WorkflowCatalog
from punch.workflow import K6Workflow, WorkflowError, read_k6_config

# Top-level k6 execution shortcuts; a sized producer config replaces them.
SHORTCUT_KEYS = ("vus", "iterations", "duration", "stages")
# Scenario keys a sized producer scenario keeps from the producer's config
# (executor-independent ones, plus maxDuration, valid for shared-iterations).
KEPT_SCENARIO_KEYS = ("exec", "env", "tags", "options", "startTime", "gracefulStop", "maxDuration")
ESTIMABLE_EXECUTORS = ("shared-iterations", "per-vu-iterations", "constant-vus")

_DURATION = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h))+")
_DURATION_GROUP = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNIT_SECONDS = {
    "ms": Fraction(1, 1000), "s": Fraction(1), "m": Fraction(60), "h": Fraction(3600),
}


class SizingError(ValueError):
    """Why a producer cannot be sized for a target with this load shape."""


@dataclass(frozen=True)
class SizingPlan:
    producer: K6Workflow
    target: K6Workflow
    datasets: tuple[str, ...]
    shape: Mapping[str, Any]
    rows_needed: int
    margin: float
    iterations: int
    vus: int
    estimated_seconds: float
    preset: str | None = None
    # The k6 options JSON the sized producer runs with.
    config: Mapping[str, Any] = field(default_factory=dict)


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


def _positive_int(source: Mapping[str, Any], name: str) -> int:
    value = source.get(name, 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SizingError(f"invalid {name} {json.dumps(value)}")
    return value


def _single_scenario(config: Mapping[str, Any], owner: str) -> tuple[str, Mapping[str, Any]] | None:
    """The one scenario `config` declares, or None when it uses top-level shortcuts."""
    scenarios = config.get("scenarios")
    if scenarios is None:
        return None
    if not isinstance(scenarios, dict) or len(scenarios) != 1:
        count = len(scenarios) if isinstance(scenarios, dict) else 0
        raise SizingError(f"{owner} declares {count} scenarios; sizing needs exactly one")
    [(name, scenario)] = scenarios.items()
    if not isinstance(scenario, dict):
        raise SizingError(f"{owner} scenario {name} must be an object")
    return name, scenario


def target_shape(config: Mapping[str, Any]) -> dict[str, Any]:
    """The executor, VUs, and iterations or duration k6 runs `config` with."""
    scenario = _single_scenario(config, "config")
    if scenario is not None:
        source = scenario[1]
        executor = source.get("executor")
    elif config.get("stages"):
        raise SizingError("stages are not estimable")
    else:
        # k6's shortcut rules: iterations → shared-iterations (duration only
        # caps it); duration alone → constant-vus; neither → one iteration on
        # one VU, whatever vus says.
        source = config if "iterations" in config or "duration" in config else {}
        has_duration = "duration" in config and "iterations" not in config
        executor = "constant-vus" if has_duration else "shared-iterations"
    if executor not in ESTIMABLE_EXECUTORS:
        raise SizingError(f"executor {json.dumps(executor)} is not estimable")
    shape: dict[str, Any] = {"executor": executor, "vus": _positive_int(source, "vus")}
    if executor == "constant-vus":
        duration = source.get("duration")
        try:
            parse_duration(duration if isinstance(duration, str) else "")
        except ValueError:
            raise SizingError(f"invalid duration {json.dumps(duration)}") from None
        shape["duration"] = duration
    else:
        shape["iterations"] = _positive_int(source, "iterations")
    return shape


def is_sizable_producer(workflow: K6Workflow) -> bool:
    sizing = workflow.sizing
    return (
        sizing is not None
        and sizing.iteration_seconds is not None
        and sizing.max_seconds is not None
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


def rows_needed(target: K6Workflow, config: Mapping[str, Any]) -> int:
    """Rows one target run with `config` reads (one per iteration), or a SizingError reason."""
    shape = target_shape(config)
    if shape["executor"] == "shared-iterations":
        return shape["iterations"]
    if shape["executor"] == "per-vu-iterations":
        return shape["vus"] * shape["iterations"]
    if target.sizing is None or target.sizing.iteration_seconds is None:
        raise SizingError(f"{target.name} declares no sizing.iterationSeconds")
    seconds = parse_duration(shape["duration"])
    return math.ceil(shape["vus"] * seconds / _exact(target.sizing.iteration_seconds))


def producer_config(producer: K6Workflow, iterations: int, vus: int) -> dict[str, Any]:
    """The producer's own config with its execution replaced by the sized shape."""
    try:
        base = read_k6_config(producer.k6_config) if producer.k6_config is not None else {}
    except WorkflowError as error:
        raise SizingError(str(error)) from None
    config = {key: value for key, value in base.items() if key not in SHORTCUT_KEYS}
    scenario = _single_scenario(base, f"{producer.name} config")
    if scenario is None:
        return {**config, "vus": vus, "iterations": iterations}
    name, source = scenario
    kept = {key: value for key, value in source.items() if key in KEPT_SCENARIO_KEYS}
    config["scenarios"] = {
        name: {**kept, "executor": "shared-iterations", "vus": vus, "iterations": iterations}
    }
    return config


def size_producer(
    producer: K6Workflow,
    target_name: str,
    catalog: WorkflowCatalog,
    config: Mapping[str, Any],
    *,
    preset: str | None = None,
) -> SizingPlan:
    """Size `producer` for one `target_name` run with the k6 options `config`."""
    if not is_sizable_producer(producer):
        raise SizingError(
            f"{producer.name} is not a sizable producer: needs sizing.iterationSeconds "
            "and sizing.maxSeconds"
        )
    assert producer.sizing is not None
    products = producer.data.produces if producer.data is not None else ()
    datasets = tuple(product.dataset for product in products if target_name in product.targets)
    if not datasets:
        raise SizingError(f"{producer.name} does not produce data for {target_name}")
    target = catalog.workflows[target_name]
    if target.sizing is None:
        raise SizingError(f"{target_name} declares no spec.sizing")
    needed = rows_needed(target, config)
    iterations = math.ceil(needed * (1 + _exact(target.sizing.margin)))
    pace = _exact(producer.sizing.iteration_seconds)
    budget = _exact(producer.sizing.max_seconds)
    vus = min(iterations, max(1, math.ceil(iterations * pace / budget)))
    return SizingPlan(
        producer=producer,
        target=target,
        datasets=datasets,
        shape=target_shape(config),
        rows_needed=needed,
        margin=target.sizing.margin,
        iterations=iterations,
        vus=vus,
        estimated_seconds=float(iterations * pace / vus),
        preset=preset,
        config=producer_config(producer, iterations, vus),
    )


def write_producer_config(plan: SizingPlan, directory: Path) -> Path:
    """Write the sized producer's k6 options JSON for `k6 run --config`."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"k6-config-{plan.producer.name}.json"
    path.write_text(json.dumps(plan.config, indent=2) + "\n", encoding="utf-8")
    return path


def _rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


def _decimal(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(float(value))


def _shape_text(plan: SizingPlan) -> str:
    shape = dict(plan.shape)
    executor = shape.pop("executor")
    return " ".join([executor, *(f"{name}={value}" for name, value in shape.items())])


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

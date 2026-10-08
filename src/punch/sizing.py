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

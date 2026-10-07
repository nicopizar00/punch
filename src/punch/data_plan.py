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


class PickerUnavailable(Exception):
    """The chooser cannot show a picker (no usable terminal)."""


# A Chooser may raise PickerUnavailable; plan_data lets it propagate.
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
                f"{_relative(workflow, path)} "
                f"({(f'{rows} row' if rows == 1 else f'{rows} rows') if rows else 'no rows'})",
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

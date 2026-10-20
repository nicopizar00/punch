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

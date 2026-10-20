"""Plan and run a Punch workflow: Punch jobs in order, each step reading the
datasets that earlier steps write.

Standard-library only. `first_jobs` and `next_jobs` list the jobs a workflow
can start with and continue with, from the catalog's dataset links.
`plan_workflow` links every read to the latest earlier writer, then resolves
each step's k6 options from the last step back: an AUTO step that writes data
for a later step is sized for that reader with `punch.sizing.size_producer`,
so a sized reader passes its own sized config to the step before it.
`run_workflow` runs the plan, one `execute_job` call (one Docker Compose run)
per step, stops at the first failure, and records
<state_dir>/punch-workflow.json before step 1, after every step, and on
interruption.
Spec: docs/specs/spec-menu-workflow.md.
"""

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


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def plan_workflow(jobs: Sequence[str], loads: Sequence[Load], catalog: JobCatalog) -> WorkflowPlan:
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


def _sizing_reader(step: WorkflowStep, by_number: dict[int, WorkflowStep]) -> WorkflowStep | None:
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
    stderr: IO[str] | None = None,
) -> int:
    """Run every step in order, one Docker Compose run each; stop at the first
    failure. Returns 0, or the failed step's k6 exit code (1 when k6 exited 0 or
    never ran). A KeyboardInterrupt marks the running step, records the
    evidence, and propagates."""
    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    terminal = stdin if stdin is not None else sys.stdin
    for step in plan.steps:
        if step.sizing is not None:
            step.config_path = write_producer_config(step.sizing, state_dir)
    evidence = _evidence(plan)
    path = state_dir / EVIDENCE_NAME
    _write_evidence(path, evidence)
    by_number = {step.number: step for step in plan.steps}
    exit_code = 0
    # dataset -> the file a step of this run published
    written: dict[str, Path] = {}
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
        written.update((data.dataset, data.path) for data in result.datasets if data.published)
        _record_result(record, step, result)
        if result.passed:
            print_metrics(step.job, output)
            for warning in _shortfall(step, record["rows"]):
                evidence["warnings"].append(warning)
                output.write(f"[punch] warning: {warning}\n")
        else:
            exit_code = result.child_exit_code or 1
            reason = result.failure or f"exit code {exit_code}"
            errors.write(f"[punch] step {step.number} {step.name} failed: {reason}\n")
        _write_evidence(path, evidence)
    evidence["outcome"] = "passed" if exit_code == 0 else "failed"
    evidence["passed"] = exit_code == 0
    _write_evidence(path, evidence)
    if exit_code == 0:
        output.write(f"[punch] workflow passed: {_count(len(plan.steps), 'step')}.\n")
    else:
        errors.write(f"[punch] {_failed_summary(evidence)}.\n")
    output.write(f"[punch] evidence written: {_display(path)}\n")
    if written:
        confirm_delete_consumed(written, stdin=terminal, stdout=output)
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
                "sizing": _planned_sizing(step.sizing) if step.sizing is not None else None,
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
    rows = {data.dataset: data.record_count for data in result.datasets if data.published}
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


def _planned_sizing(sizing: SizingPlan) -> dict[str, Any]:
    """Sizing evidence before the step runs: nothing produced yet."""
    return {**sizing_evidence(sizing, {}), "producedRows": None, "short": None}


def _failed_summary(evidence: Mapping[str, Any]) -> str:
    """Where the workflow stopped, and which later steps it skipped."""
    failed = next(record for record in evidence["steps"] if record["status"] == "failed")
    skipped = [str(record["step"]) for record in evidence["steps"] if record["status"] == "skipped"]
    text = f"workflow failed at step {failed['step']} {failed['job']}"
    if skipped:
        text += f"; {'step' if len(skipped) == 1 else 'steps'} {_and(skipped)} skipped"
    return text

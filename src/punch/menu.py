"""Interactive picker for k6 workflow YAML files.

Generic over any directory of `punch/v1` `K6Workflow` YAMLs — a consumer
repo's own workflows directory, or Punch's own bundled `workflows/k6/`.
Reuses the same `punch.execution` machinery `punch run` uses; this is a
picker on top of it, not a replacement.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from rich.cells import cell_len, set_cell_size
from simple_term_menu import TerminalMenu

from punch.catalog import CatalogError, load_catalog
from punch.data_plan import Choice, PickerUnavailable, PlanStop, plan_data, switch_hint
from punch.execution import (
    ExecutionResult,
    build_compose_run_command,
    confirm_delete_consumed,
    confirm_docker_run,
    data_environment,
    execute_workflow,
    used_data_paths,
)
from punch.workflow import K6Workflow, WorkflowError, load_workflow


def discover_workflows(workflows_dir: Path) -> List[Path]:
    return sorted(workflows_dir.glob("*.yaml"))


def discover_options(options_dir: Path) -> List[Path]:
    if not options_dir.is_dir():
        return []
    return sorted(options_dir.glob("*.json"))


def _prompt(question: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or (default or "")


class _MenuCancelled(Exception):
    """An operator left a selection menu without choosing an action."""


class _MenuUnavailable(Exception):
    """The terminal menu could not acquire a controlling terminal."""


def _select(entries: List[str], title: str, cursor_index: int = 0) -> int:
    # Compose confirmation reads sys.stdin; never let the picker read /dev/tty
    # while redirected stdin silently disables that confirmation.
    if not sys.stdin.isatty():
        raise _MenuUnavailable
    try:
        selection = TerminalMenu(entries, title=title, cursor_index=cursor_index).show()
    except (OSError, NotImplementedError) as error:
        raise _MenuUnavailable from error
    if selection is None:
        raise _MenuCancelled
    return selection


def _required_input(workflow: K6Workflow) -> str:
    if workflow.data is None:
        return "—"
    names = [*workflow.data.requires, *(f"{d}?" for d in workflow.data.optional)]
    return ", ".join(names) or "—"


def _generated_output(workflow: K6Workflow) -> str:
    if workflow.data is None or not workflow.data.produces:
        return "—"
    return ", ".join(product.dataset for product in workflow.data.produces)


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _terminal_columns() -> int:
    try:
        return os.get_terminal_size(sys.stdin.fileno()).columns
    except (AttributeError, OSError, ValueError):
        return shutil.get_terminal_size().columns


def _workflow_menu_rows(paths: List[Path]) -> tuple[List[str], str]:
    headers = ("Name", "Description", "In", "Out")
    records = []
    for path in paths:
        try:
            workflow = load_workflow(path)
        except WorkflowError as error:
            records.append(
                (_single_line(path.stem), _single_line(f"invalid: {error}"), "—", "—")
            )
            continue
        records.append(
            (
                _single_line(path.stem),
                _single_line(workflow.description) or "—",
                _single_line(_required_input(workflow)),
                _single_line(_generated_output(workflow)),
            )
        )

    desired_widths = [
        max(cell_len(header), *(cell_len(record[index]) for record in records))
        for index, header in enumerate(headers)
    ]
    separator = " │ "
    separator_width = cell_len(separator) * (len(headers) - 1)
    available_width = max(
        len(headers), _terminal_columns() - cell_len("> ") - separator_width
    )
    preferred_widths = [desired_widths[0], *(cell_len(header) for header in headers[1:])]
    widths = (
        preferred_widths.copy()
        if sum(preferred_widths) <= available_width
        else [1] * len(headers)
    )
    remaining_width = available_width - sum(widths)

    while remaining_width > 0:
        grew = False
        for index in range(len(widths)):
            if widths[index] < desired_widths[index]:
                widths[index] += 1
                remaining_width -= 1
                grew = True
                if remaining_width == 0:
                    break
        if not grew:
            break

    def format_row(values: tuple[str, str, str, str]) -> str:
        cells = []
        for value, width in zip(values, widths):
            if cell_len(value) > width:
                value = (
                    "…" if width == 1 else set_cell_size(value, width - 1).rstrip() + "…"
                )
            cells.append(set_cell_size(value, width))
        return separator.join(cells)

    title = "Available k6 workflows\n" + format_row(headers)
    rows = [format_row(record).replace("|", r"\|") for record in records]
    return rows, title


def _choose_workflow(paths: List[Path]) -> Path:
    rows, title = _workflow_menu_rows(paths)
    return paths[_select(rows, title)]


def choose(choice: Choice) -> Optional[int]:
    """Render a Choice with the shared arrow-key menu; Esc → None; no usable terminal → PickerUnavailable."""
    try:
        return _select(list(choice.options), choice.title, cursor_index=choice.cursor)
    except _MenuCancelled:
        return None
    except _MenuUnavailable as error:
        raise PickerUnavailable from error


def _read_base_url(path: Path) -> Optional[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("baseUrl")
    return value if isinstance(value, str) and value else None


def _default_base_url(compose_service: str) -> Optional[str]:
    # Different compose services usually target different processes (e.g. a
    # browser-driven workflow's service points at a web app, not the same
    # backend every other service targets), so docker-host.json alone can't
    # be a correct default for all of them. A same-named
    # environment/<service>-host.json takes precedence when present; every
    # workflow still falls back to the single docker-host.json otherwise.
    environment_dir = Path(__file__).resolve().parents[2] / "environment"
    for path in (
        environment_dir / f"{compose_service}-host.json",
        environment_dir / "docker-host.json",
    ):
        value = _read_base_url(path)
        if value is not None:
            return value
    return None


def _choose_base_url(workflow: K6Workflow) -> Optional[str]:
    if "BASE_URL" not in workflow.forward_environment:
        return None
    current = os.environ.get("BASE_URL") or _default_base_url(workflow.compose_service)
    choice = _select([f"Current ({current or 'unset'})", "Custom URL"], "Pick a target:")
    if choice == 1:
        return _prompt("Enter BASE_URL", default=current or "")
    return current or None


def _load_options_preset(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"[punch] could not read options preset {path.name}: {error}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"[punch] options preset {path.name} must be a JSON object; ignoring.", file=sys.stderr)
        return {}
    return {str(key): str(value) for key, value in data.items()}


_DEFAULT_OPTIONS_PRESET = "5-iterations"


def _choose_options(workflow: K6Workflow, options_dir: Path) -> dict:
    extra_names = [name for name in workflow.forward_environment if name != "BASE_URL"]
    if not extra_names:
        return {}
    paths = discover_options(options_dir)
    if not paths:
        return {}
    entries = ["Skip (use env/default)"] + [path.stem for path in paths]
    # 5 iterations is the repo-wide default load shape — environment
    # independent (a fixed op count), unlike a DURATION-based soak whose
    # throughput varies with how fast the target environment is. Pre-select
    # it when available; every preset stays choosable regardless of which
    # vars this workflow forwards.
    default_index = entries.index(_DEFAULT_OPTIONS_PRESET) if _DEFAULT_OPTIONS_PRESET in entries else 0
    choice = _select(entries, "Load an options preset:", cursor_index=default_index)
    if choice == 0:
        return {}
    return _load_options_preset(paths[choice - 1])


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


def _print_metrics(workflow: K6Workflow) -> None:
    if workflow.summary_output is None or not workflow.summary_output.path.exists():
        return
    try:
        summary = json.loads(workflow.summary_output.path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    print(f"[punch] {workflow.name} metrics:")
    print(f"  requests    : {summary.get('totalRequests', '?')}")
    print(f"  error rate  : {summary.get('errorRate', 0) * 100:.2f}%")
    print(f"  p90 duration: {summary.get('p90Ms', 0):.1f} ms")
    print(f"  check pass  : {summary.get('checkPassRate', 0) * 100:.2f}%")
    print(f"  duration    : {summary.get('durationMs', 0) / 1000:.1f}s")


def _report(workflow: K6Workflow, result: ExecutionResult) -> int:
    if result.passed:
        print(f"[punch] {workflow.name} completed.")
        return result.child_exit_code or 0
    if result.child_exit_code:
        print(f"[punch] {workflow.name} failed (exit code {result.child_exit_code}).", file=sys.stderr)
        return result.child_exit_code
    print(f"[punch] {workflow.name} failed: {result.failure}", file=sys.stderr)
    return 1


def _choose_top_level_action() -> int:
    return _select(["Run workflow", "Monitoring setup"], "[punch] Punch orchestrator.")


def _monitoring_setup() -> int:
    print("[punch] monitoring setup: not implemented yet.")
    return 0


def run_menu(workflows_dir: Path, options_dir: Optional[Path] = None) -> int:
    try:
        action = _choose_top_level_action()
        if action == 1:
            return _monitoring_setup()
        return _run_workflow_menu(workflows_dir, options_dir)
    except _MenuCancelled:
        print("[punch] menu canceled.")
        return 0
    except _MenuUnavailable:
        print("[punch] interactive menu requires a terminal.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[punch] workflow interrupted.", file=sys.stderr)
        return 130


def _run_workflow_menu(workflows_dir: Path, options_dir: Optional[Path] = None) -> int:
    resolved_options_dir = options_dir if options_dir is not None else workflows_dir.parent / "options"
    paths = discover_workflows(workflows_dir)
    if not paths:
        print(f"[punch] no workflow YAMLs found under {workflows_dir}")
        return 1

    print()
    selected = _choose_workflow(paths)
    try:
        workflow = load_workflow(selected)
        catalog = load_catalog(workflows_dir)
    except (WorkflowError, CatalogError) as error:
        print(f"[punch] could not load workflow {selected.stem}: {error}", file=sys.stderr)
        return 1

    try:
        outcome = plan_data(workflow, catalog, {}, {}, choose=choose, environment=os.environ)
    except PickerUnavailable:
        raise _MenuUnavailable from None
    if isinstance(outcome, PlanStop):
        if outcome.canceled:
            raise _MenuCancelled
        print(f"[punch] {outcome.reason}", file=sys.stderr)
        return 1
    plan = outcome
    workflow, choices = plan.workflow, plan.optional_choices

    base_url = _choose_base_url(workflow)
    options = _choose_options(workflow, resolved_options_dir)
    produce = _choose_produce(workflow, plan.produce)

    environment = dict(os.environ)
    if base_url is not None:
        environment["BASE_URL"] = base_url
    environment.update(options)

    command = build_compose_run_command(
        workflow, environment, data_env=data_environment(workflow, {}, choices)
    )
    docker_run_confirmed = confirm_docker_run(
        command, assume_yes=False, stdin=sys.stdin, stdout=sys.stdout
    )

    print()
    result = execute_workflow(
        workflow,
        environment=environment,
        produce=produce,
        optional_choices=choices,
        producers_of=catalog.producers_of,
        docker_run_confirmed=docker_run_confirmed,
    )
    rc = _report(workflow, result)
    if result.child_exit_code is not None:
        confirm_delete_consumed(
            used_data_paths(workflow, {}, choices), stdin=sys.stdin, stdout=sys.stdout
        )
    if result.passed:
        _print_metrics(workflow)
        if plan.switched_from:
            print(switch_hint(plan, catalog))
    print()
    return rc

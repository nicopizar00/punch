"""Interactive picker for k6 workflow YAML files.

Generic over any directory of `punch/v1` `K6Workflow` YAMLs — a consumer
repo's own workflows directory, or Punch's own bundled `workflows/k6/`.
Reuses the same `punch.execution` machinery `punch run` uses; this is a
picker on top of it, not a replacement.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from simple_term_menu import TerminalMenu

from punch.execution import (
    ExecutionResult,
    build_compose_run_command,
    confirm_docker_run,
    execute_workflow,
)
from punch.workflow import K6Workflow, WorkflowError, load_workflow


def discover_workflows(workflows_dir: Path) -> List[Path]:
    return sorted(workflows_dir.glob("*.yaml"))


def _prompt(question: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or (default or "")


class _MenuCancelled(Exception):
    """An operator left a selection menu without choosing an action."""


class _MenuUnavailable(Exception):
    """The terminal menu could not acquire a controlling terminal."""


def _select(entries: List[str], title: str) -> int:
    # Compose confirmation reads sys.stdin; never let the picker read /dev/tty
    # while redirected stdin silently disables that confirmation.
    if not sys.stdin.isatty():
        raise _MenuUnavailable
    try:
        selection = TerminalMenu(entries, title=title).show()
    except (OSError, NotImplementedError) as error:
        raise _MenuUnavailable from error
    if selection is None:
        raise _MenuCancelled
    return selection


def _choose_workflow(paths: List[Path]) -> Path:
    return paths[_select([path.stem for path in paths], "Available k6 workflows:")]


def _default_base_url() -> Optional[str]:
    path = Path(__file__).resolve().parents[2] / "environment" / "docker-host.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("baseUrl")
    return value if isinstance(value, str) and value else None


def _choose_base_url(workflow: K6Workflow) -> Optional[str]:
    if "BASE_URL" not in workflow.forward_environment:
        return None
    current = os.environ.get("BASE_URL") or _default_base_url()
    choice = _select([f"Current ({current or 'unset'})", "Custom URL"], "Pick a target:")
    if choice == 1:
        return _prompt("Enter BASE_URL", default=current or "")
    return current or None


def _choose_confirm_output_data(workflow: K6Workflow) -> bool:
    if workflow.csv_output is None:
        return True
    answer = _prompt(
        f"Workflow declares CSV output at {workflow.csv_output.path} — write it? (y/N)",
        default="n",
    )
    return answer.lower().startswith("y")


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


def run_menu(workflows_dir: Path) -> int:
    try:
        action = _choose_top_level_action()
        if action == 1:
            return _monitoring_setup()
        return _run_workflow_menu(workflows_dir)
    except _MenuCancelled:
        print("[punch] menu canceled.")
        return 0
    except _MenuUnavailable:
        print("[punch] interactive menu requires a terminal.", file=sys.stderr)
        return 1


def _run_workflow_menu(workflows_dir: Path) -> int:
    paths = discover_workflows(workflows_dir)
    if not paths:
        print(f"[punch] no workflow YAMLs found under {workflows_dir}")
        return 1

    print()
    selected = _choose_workflow(paths)
    try:
        workflow = load_workflow(selected)
    except WorkflowError as error:
        print(f"[punch] could not load workflow {selected.stem}: {error}", file=sys.stderr)
        return 1

    base_url = _choose_base_url(workflow)
    confirmed = _choose_confirm_output_data(workflow)

    environment = dict(os.environ)
    if base_url is not None:
        environment["BASE_URL"] = base_url

    command = build_compose_run_command(workflow, environment)
    docker_run_confirmed = confirm_docker_run(
        command, assume_yes=False, stdin=sys.stdin, stdout=sys.stdout
    )

    print()
    result = execute_workflow(
        workflow,
        environment=environment,
        output_data_confirmed=confirmed,
        docker_run_confirmed=docker_run_confirmed,
    )
    rc = _report(workflow, result)
    if result.passed:
        _print_metrics(workflow)
    print()
    return rc

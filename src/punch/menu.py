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


def _choose_workflow(paths: List[Path]) -> Path:
    print("Available k6 workflows:")
    for index, path in enumerate(paths, start=1):
        print(f"  {index}) {path.stem}")
    print()
    while True:
        choice = _prompt("Pick a workflow number", default="1")
        try:
            return paths[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice, try again.")


def _choose_base_url(workflow: K6Workflow) -> Optional[str]:
    if "BASE_URL" not in workflow.forward_environment:
        return None
    current = os.environ.get("BASE_URL", "")
    print()
    print(f"1) Current ({current or 'unset'})")
    print("2) Custom URL")
    choice = _prompt("Pick a target", default="1")
    if choice == "2":
        return _prompt("Enter BASE_URL", default=current)
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


def _choose_top_level_action() -> str:
    print("[punch] Punch orchestrator.")
    print()
    print("1) Run workflow")
    print("2) Monitoring setup")
    print()
    while True:
        choice = _prompt("Pick an option", default="1")
        if choice in ("1", "2"):
            return choice
        print("Invalid choice, try again.")


def _monitoring_setup() -> int:
    print("[punch] monitoring setup: not implemented yet.")
    return 0


def run_menu(workflows_dir: Path) -> int:
    action = _choose_top_level_action()
    if action == "2":
        return _monitoring_setup()
    return _run_workflow_menu(workflows_dir)


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

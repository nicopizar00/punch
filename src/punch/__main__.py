"""punch CLI entry point.

Python orchestration with pinned PyYAML for workflow loading. Owns control
flow; delegates execution to Docker Compose. Writes a single evidence file at
reports/state/punch-run.json so automation can confirm a run happened without
parsing k6 output. Install the pinned requirements before invoking Punch.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import socket
from urllib.parse import urlparse

from punch.data_plan import DataPlan, PickerUnavailable, PlanStop, plan_data, switch_hint

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "reports"
STATE_DIR = REPORTS_DIR / "state"
LOGS_DIR = REPORTS_DIR / "logs"
SERVICE_LOG_NAMES = ("gateway-api", "catalog-api", "orders-api", "postgres")

BUNDLED_WORKFLOW_DIR = REPO_ROOT / "workflows" / "k6"
BUNDLED_WORKFLOWS = {
    "smoke": BUNDLED_WORKFLOW_DIR / "smoke.yaml",
    "gate": BUNDLED_WORKFLOW_DIR / "gate.yaml",
    "journey": BUNDLED_WORKFLOW_DIR / "journey.yaml",
    "bff-checkout-journey": BUNDLED_WORKFLOW_DIR / "bff-checkout-journey.yaml",
}


def _ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _stream(cmd: list[str], log_path: Path | None = None) -> int:
    """Run a subprocess streaming stdout+stderr to terminal and optional log."""
    print(f"$ {' '.join(cmd)}", flush=True)
    log_fh = log_path.open("w", encoding="utf-8") if log_path else None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if log_fh:
                log_fh.write(line)
        return proc.wait()
    finally:
        if log_fh:
            log_fh.close()


def _write_evidence(record: dict) -> None:
    _ensure_dirs()
    path = STATE_DIR / "punch-run.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"[punch] evidence written: {path.relative_to(REPO_ROOT)}", flush=True)


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []

    docker = shutil.which("docker")
    checks.append(("docker on PATH", docker is not None, docker or "missing"))

    compose_ok = False
    compose_version = ""
    if docker:
        try:
            out = subprocess.run(
                [docker, "compose", "version"],
                capture_output=True, text=True, check=False,
            )
            compose_ok = out.returncode == 0
            compose_version = (out.stdout or out.stderr).strip().splitlines()[0] if out.stdout or out.stderr else ""
        except OSError as e:
            compose_version = str(e)
    checks.append(("docker compose available", compose_ok, compose_version))

    compose_file = REPO_ROOT / "docker-compose.yml"
    checks.append(("docker-compose.yml present", compose_file.is_file(), str(compose_file)))

    py_ok = sys.version_info >= (3, 10)
    checks.append(("python >= 3.10", py_ok, sys.version.split()[0]))

    try:
        import yaml
        yaml_ok = True
        yaml_detail = getattr(yaml, "__version__", "available")
    except ModuleNotFoundError:
        yaml_ok = False
        yaml_detail = "missing"
    checks.append(("PyYAML available", yaml_ok, yaml_detail))

    workflow_ok = False
    workflow_detail = "PyYAML missing"
    if yaml_ok:
        try:
            from punch.workflow import WorkflowError, load_workflow
            for path in BUNDLED_WORKFLOWS.values():
                load_workflow(path)
            workflow_ok = True
            workflow_detail = f"{len(BUNDLED_WORKFLOWS)} validated"
        except (ImportError, WorkflowError) as error:
            workflow_detail = str(error)
    checks.append(("bundled workflows valid", workflow_ok, workflow_detail))

    for name, ok, detail in checks:
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] {name}: {detail}")

    return 0 if all(ok for _, ok, _ in checks) else 1


def _diagnose_target_connectivity() -> None:
    """Attempt a best-effort TCP connect to TARGET_BASE_URL to help diagnosis.

    This runs on the host (not inside the container) and is only advisory; it
    does not change orchestration behavior but helps the operator understand
    why a containerized k6 run might fail to reach a host-provided service.
    """
    target = os.environ.get("TARGET_BASE_URL")
    if not target:
        return
    try:
        p = urlparse(target)
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
        print(f"[punch] diagnosing TARGET_BASE_URL reachability: {host}:{port}", flush=True)
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"[punch] host reachable: {host}:{port}", flush=True)
        except OSError as e:
            print(f"[punch] host NOT reachable from host: {host}:{port} ({e})", flush=True)
    except Exception as e:
        print(f"[punch] could not parse TARGET_BASE_URL '{target}': {e}", flush=True)


def _collect_service_logs() -> None:
    for svc in SERVICE_LOG_NAMES:
        log = LOGS_DIR / f"{svc}.log"
        try:
            with log.open("w", encoding="utf-8") as fh:
                subprocess.run(
                    ["docker", "compose", "logs", svc],
                    cwd=REPO_ROOT, stdout=fh, stderr=subprocess.STDOUT, check=False,
                )
        except OSError as e:
            print(f"[punch] could not collect logs for {svc}: {e}", flush=True)


def _load_selected_workflows(selector: str):
    from punch.workflow import WorkflowError, load_workflow

    paths = list(BUNDLED_WORKFLOWS.values()) if selector == "all" else [
        BUNDLED_WORKFLOWS.get(selector, Path(selector))
    ]
    workflows = []
    for path in paths:
        if not path.is_file():
            raise WorkflowError(f"workflow file does not exist: {path}")
        workflows.append(load_workflow(path))
    return workflows


def _effective_exit_code(result) -> int:
    if result.passed:
        return result.child_exit_code or 0
    return result.child_exit_code or 1


def _evidence_result(
    workflow,
    result,
    *,
    skipped: bool = False,
    switched_from: tuple[str, ...] = (),
    data_sources: dict[str, str] | None = None,
    sizing: dict | None = None,
    config: Path | None = None,
) -> dict:
    return {
        "test": workflow.name,
        "workflow": str(workflow.source_path.relative_to(workflow.working_directory)),
        "exitCode": _effective_exit_code(result),
        "childExitCode": result.child_exit_code,
        "passed": result.passed,
        "failure": result.failure,
        "datasets": [
            {
                "dataset": dataset.dataset,
                "path": str(dataset.path.relative_to(workflow.working_directory)),
                "recordCount": dataset.record_count,
                "published": dataset.published,
            }
            for dataset in result.datasets
        ],
        **({"skipped": True} if skipped else {}),
        **({"switchedFrom": list(switched_from)} if switched_from else {}),
        **({"dataSources": data_sources} if data_sources else {}),
        **({"sizing": sizing} if sizing else {}),
        **({"config": _evidence_path(workflow, config)} if config is not None else {}),
    }


def _evidence_path(workflow, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workflow.working_directory))
    except ValueError:
        return str(path.resolve())


def cmd_run(args: argparse.Namespace) -> int:
    from punch.catalog import CatalogError, load_catalog
    from punch.execution import (
        ExecutionResult,
        confirm_delete_consumed,
        data_row_count,
        execute_workflow,
        paths_collide,
        used_data_paths,
        data_sources,
        resolve_data_args,
        validate_produce,
    )
    from punch.workflow import WorkflowError, read_k6_config
    from punch.sizing import (
        SizingError,
        input_warnings,
        next_hint,
        shortfall,
        size_producer,
        sizing_evidence,
        summary_lines,
        write_producer_config,
    )

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    if args.selector == "all" and (args.produce or args.data or args.size_for):
        print("[punch] --produce, --data, and --size-for apply to one selected workflow, not 'all'",
              file=sys.stderr, flush=True)
        return 1
    config_path = Path(args.config).resolve() if args.config else None
    try:
        workflows = _load_selected_workflows(args.selector)
        if config_path is not None:
            read_k6_config(config_path)
    except WorkflowError as error:
        print(f"[punch] {error}", file=sys.stderr, flush=True)
        return 1

    results: list[dict] = []
    runnable = []
    for workflow in workflows:
        missing = [name for name in workflow.required_environment if not os.environ.get(name)]
        if args.selector == "all" and missing:
            failure = f"missing required environment: {', '.join(missing)}"
            print(f"[punch] SKIP {workflow.name}: {failure}", flush=True)
            results.append(_evidence_result(
                workflow,
                ExecutionResult(workflow.name, (), None, True, failure),
                skipped=True,
            ))
            continue
        runnable.append(workflow)

    produce_args, data_args = list(args.produce), list(args.data)
    size_for: str | None = args.size_for
    plan: DataPlan | None = None
    stopped_rc = 0
    if (
        args.selector != "all"
        and len(runnable) == 1
        and not args.no_input
        and sys.stdin.isatty()
    ):
        from punch.menu import choose

        selected = runnable[0]
        try:
            catalog = load_catalog(selected.source_path.parent)
            overrides, preset = resolve_data_args(selected, data_args)
        except (CatalogError, ValueError):
            pass  # reported by the per-workflow loop below
        else:
            try:
                outcome = plan_data(
                    selected, catalog, overrides, preset, choose=choose, environment=os.environ
                )
            except PickerUnavailable:
                print(
                    "[punch] no interactive terminal for pickers; using automatic data sources",
                    flush=True,
                )
                outcome = None
            if outcome is None:
                pass
            elif isinstance(outcome, PlanStop):
                print(f"[punch] {outcome.reason}", file=sys.stderr, flush=True)
                results.append(_evidence_result(
                    selected, ExecutionResult(selected.name, (), None, False, outcome.reason)
                ))
                runnable, stopped_rc = [], 1
            else:
                plan = outcome
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
    switched_from = plan.switched_from if plan is not None else ()
    sizing_plan = None
    if size_for is not None and runnable:
        sized_workflow = runnable[0]
        try:
            catalog = load_catalog(sized_workflow.source_path.parent)
            # --config names the target's shape here; the producer runs sized.
            target = catalog.workflows.get(size_for)
            target_config = config_path or (target.k6_config if target is not None else None)
            sizing_plan = size_producer(
                sized_workflow,
                size_for,
                catalog,
                read_k6_config(target_config) if target_config is not None else {},
                preset=target_config.stem if target_config is not None else None,
            )
        except (CatalogError, SizingError, WorkflowError) as error:
            print(f"[punch] {error}", file=sys.stderr, flush=True)
            results.append(_evidence_result(
                sized_workflow,
                ExecutionResult(sized_workflow.name, (), None, False, str(error)),
            ))
            runnable, stopped_rc = [], 1
        else:
            produce_args = list(dict.fromkeys([*produce_args, *sizing_plan.datasets]))

    protected_paths = [STATE_DIR / "punch-run.json"]
    protected_paths.extend(LOGS_DIR / f"k6-{workflow.name}.log" for workflow in runnable)
    if args.collect_logs:
        protected_paths.extend(LOGS_DIR / f"{service}.log" for service in SERVICE_LOG_NAMES)

    # A dataset aliasing a run artifact would be clobbered (or clobber it)
    # mid-run; refuse before any Docker call or evidence write.
    for workflow in runnable:
        if workflow.data is None:
            continue
        try:
            produce = validate_produce(workflow, produce_args)
        except ValueError:
            continue  # reported per workflow below
        for dataset in produce:
            destination = workflow.data.host_path(dataset)
            if any(paths_collide(destination, path) for path in protected_paths):
                print(
                    f"[punch] data output collides with another run artifact: {destination}",
                    file=sys.stderr,
                    flush=True,
                )
                return 1

    _ensure_dirs()

    overall_rc = 0
    for workflow in runnable:
        try:
            catalog = load_catalog(workflow.source_path.parent)
            produce = validate_produce(workflow, produce_args)
            overrides, preset = resolve_data_args(workflow, data_args)
        except (CatalogError, ValueError) as error:
            print(f"[punch] {error}", file=sys.stderr, flush=True)
            results.append(_evidence_result(
                workflow,
                ExecutionResult(workflow.name, (), None, False, str(error)),
                switched_from=switched_from,
            ))
            overall_rc = overall_rc or 1
            if not args.keep_going:
                break
            continue
        choices = plan.optional_choices if plan is not None else preset
        config = config_path
        if sizing_plan is not None:
            config = write_producer_config(sizing_plan, STATE_DIR)
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
            environment=os.environ,
            produce=produce,
            data_overrides=overrides,
            optional_choices=choices,
            producers_of=catalog.producers_of,
            log_path=LOGS_DIR / f"k6-{workflow.name}.log",
            config=config,
        )
        produced = {
            dataset.dataset: dataset.record_count for dataset in result.datasets if dataset.published
        }
        results.append(_evidence_result(
            workflow,
            result,
            switched_from=switched_from,
            data_sources=data_sources(workflow, overrides, choices),
            sizing=sizing_evidence(sizing_plan, produced) if sizing_plan is not None else None,
            config=config or workflow.k6_config,
        ))
        if result.passed and switched_from:
            print(switch_hint(plan, catalog), flush=True)
        if result.passed and sizing_plan is not None:
            for line in shortfall(sizing_plan, produced):
                print(line, flush=True)
            print(next_hint(sizing_plan, produced), flush=True)
        if result.child_exit_code is not None:
            confirm_delete_consumed(
                used_data_paths(workflow, overrides, choices), stdin=sys.stdin, stdout=sys.stdout
            )
        effective_exit_code = _effective_exit_code(result)
        if not result.passed and overall_rc == 0:
            overall_rc = effective_exit_code
        if not result.passed and not args.keep_going:
            break

    if args.collect_logs:
        _collect_service_logs()

    overall_rc = overall_rc or stopped_rc
    _write_evidence({
        "command": "run",
        "tests": [workflow.name for workflow in workflows],
        "results": results,
        "exitCode": overall_rc,
        "passed": overall_rc == 0,
        "startedAt": started,
        "durationSeconds": round(time.monotonic() - t0, 2),
    })
    return overall_rc


def cmd_menu(args: argparse.Namespace) -> int:
    from punch.menu import run_menu

    workflows_dir = Path(args.workflows_dir) if args.workflows_dir else BUNDLED_WORKFLOW_DIR
    return run_menu(workflows_dir, state_dir=STATE_DIR)


def cmd_clean(_args: argparse.Namespace) -> int:
    return _stream(["docker", "compose", "down", "--volumes", "--remove-orphans"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="punch",
        description="Local orchestrator for the k6-ts-docker performance gate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check host prerequisites (docker, compose, python).")

    run_p = sub.add_parser("run", help="Run a bundled k6 workflow, all workflows, or a YAML path.")
    run_p.add_argument("selector", help="Bundled workflow name, 'all', or a workflow YAML path.")
    run_p.add_argument("--keep-going", action="store_true",
                       help="With 'all', continue subsequent tests after a failure.")
    run_p.add_argument("--collect-logs", action="store_true",
                       help="After the run, dump service logs to reports/logs/.")
    run_p.add_argument("--produce", action="append", default=[], metavar="DATASET",
                       help="Write a declared dataset (repeatable, or 'all'). Without it no data file is written.")
    run_p.add_argument("--data", action="append", default=[], metavar="DATASET=PATH",
                       help="Read a dataset from PATH (beneath spec.data.directory), or DATASET=default for an optional dataset's built-in data.")
    run_p.add_argument("--no-input", action="store_true",
                       help="Never open the data-source or producer pickers.")
    run_p.add_argument("--config", metavar="PATH", default=None,
                       help="k6 options JSON passed as `k6 run --config` "
                            "(default: the workflow's spec.k6.config).")
    run_p.add_argument("--size-for", metavar="TARGET", default=None,
                       help="Size this producer run for TARGET: rows from TARGET's k6 config "
                            "(--config, else TARGET's spec.k6.config) plus its margin; "
                            "implies --produce for the datasets it feeds TARGET.")

    menu_p = sub.add_parser(
        "menu", help="Interactively pick and run a k6 workflow from a directory."
    )
    menu_p.add_argument(
        "workflows_dir",
        nargs="?",
        default=None,
        help="Directory of workflow YAML files (default: Punch's own bundled workflows/k6).",
    )

    sub.add_parser("clean", help="Tear down compose stack and volumes.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "doctor": cmd_doctor,
        "run": cmd_run,
        "menu": cmd_menu,
        "clean": cmd_clean,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

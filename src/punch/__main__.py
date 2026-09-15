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

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "reports"
STATE_DIR = REPORTS_DIR / "state"
LOGS_DIR = REPORTS_DIR / "logs"

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
    for svc in ("gateway-api", "catalog-api", "orders-api", "postgres"):
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


def _evidence_result(workflow, result, *, skipped: bool = False) -> dict:
    return {
        "test": workflow.name,
        "workflow": str(workflow.source_path.relative_to(workflow.working_directory)),
        "exitCode": _effective_exit_code(result),
        "childExitCode": result.child_exit_code,
        "passed": result.passed,
        "failure": result.failure,
        "csvPath": (
            str(result.csv_path.relative_to(workflow.working_directory))
            if result.csv_path else None
        ),
        "csvRecordCount": result.csv_record_count,
        **({"skipped": True} if skipped else {}),
    }


def cmd_run(args: argparse.Namespace) -> int:
    from punch.execution import ExecutionResult, confirm_output_data, execute_workflow, paths_collide
    from punch.workflow import WorkflowError

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    try:
        workflows = _load_selected_workflows(args.selector)
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
                ExecutionResult(workflow.name, (), None, True, failure, workflow.csv_output.path if workflow.csv_output else None, 0),
                skipped=True,
            ))
            continue
        runnable.append(workflow)

    for workflow in runnable:
        if workflow.csv_output is None:
            continue
        protected_paths = (
            STATE_DIR / "punch-run.json",
            LOGS_DIR / f"k6-{workflow.name}.log",
        )
        if any(paths_collide(workflow.csv_output.path, path) for path in protected_paths):
            print(
                f"[punch] CSV output collides with a Punch artifact: {workflow.csv_output.path}",
                file=sys.stderr,
                flush=True,
            )
            return 1

    _ensure_dirs()

    if not confirm_output_data(
        runnable, assume_yes=args.confirm_output_data, stdin=sys.stdin, stdout=sys.stdout
    ):
        print("[punch] CSV output requires --confirm-output-data in noninteractive mode", file=sys.stderr)
        for workflow in runnable:
            if workflow.csv_output is not None:
                results.append(_evidence_result(
                    workflow,
                    ExecutionResult(
                        workflow.name,
                        (),
                        None,
                        False,
                        "CSV output requires confirmation",
                        workflow.csv_output.path,
                        0,
                    ),
                ))
        _write_evidence({
            "command": "run", "tests": [workflow.name for workflow in workflows], "results": results,
            "exitCode": 1, "passed": False, "startedAt": started,
            "durationSeconds": round(time.monotonic() - t0, 2),
        })
        return 1

    overall_rc = 0
    for workflow in runnable:
        result = execute_workflow(
            workflow,
            environment=os.environ,
            output_data_confirmed=True,
            log_path=LOGS_DIR / f"k6-{workflow.name}.log",
        )
        results.append(_evidence_result(workflow, result))
        effective_exit_code = _effective_exit_code(result)
        if not result.passed and overall_rc == 0:
            overall_rc = effective_exit_code
        if not result.passed and not args.keep_going:
            break

    if args.collect_logs:
        _collect_service_logs()

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


def cmd_clean(_args: argparse.Namespace) -> int:
    return _stream(["docker", "compose", "down", "--volumes", "--remove-orphans"])


def cmd_init(args: argparse.Namespace) -> int:
    # Lazy import: the scanner is only needed for this command and keeps the
    # hot path (doctor/run/clean) free of the extra module.
    from punch import init_scan
    return init_scan.run(args)


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
    run_p.add_argument("--confirm-output-data", action="store_true",
                       help="Allow declared CSV output without an interactive confirmation.")

    sub.add_parser("clean", help="Tear down compose stack and volumes.")

    init_p = sub.add_parser(
        "init",
        help="First-wave bootstrap scan: map Copilot assets + docs readiness for Punch adoption.",
    )
    init_p.add_argument("--dry-run", action="store_true",
                        help="Compute and print the scan; write nothing (default behavior).")
    init_p.add_argument("--write", action="store_true",
                        help="Persist the generated bootstrap artifacts to the output dir.")
    init_p.add_argument("--with-graphify", action="store_true",
                        help="Explicit opt-in marker for the lightweight Graphify "
                             "availability check. Init always records Graphify "
                             "file-readiness and never shells out to the CLI "
                             "(dependency-free); this flag only annotates that the "
                             "check was requested. Never required.")
    init_p.add_argument("--output", metavar="DIR", default=None,
                        help="Output dir for generated artifacts (default: docs/ai/governance/init).")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "doctor": cmd_doctor,
        "run": cmd_run,
        "clean": cmd_clean,
        "init": cmd_init,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

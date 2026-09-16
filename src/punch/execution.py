from __future__ import annotations

import csv
import os
import queue
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Mapping, Sequence

from punch.workflow import K6Workflow


CSV_TAG = "[CSV]"
PROCESS_STOP_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class ExecutionResult:
    workflow_name: str
    command: tuple[str, ...]
    child_exit_code: int | None
    passed: bool
    failure: str | None
    csv_path: Path | None
    csv_record_count: int


def build_compose_run_command(
    workflow: K6Workflow,
    environment: Mapping[str, str],
) -> list[str]:
    command = [
        "docker",
        "compose",
        "-f",
        str(workflow.compose_file),
        "run",
        "--rm",
    ]
    for name in workflow.forward_environment:
        if name in environment:
            command.extend(["-e", f"{name}={environment[name]}"])
    command.extend([workflow.compose_service, "run", workflow.k6_script])
    return command


def confirm_docker_run(
    command: Sequence[str], *, assume_yes: bool, stdin: IO[str], stdout: IO[str]
) -> bool:
    """Ask before Docker Compose runs, since `compose run` may build images.

    Only interactive terminals are prompted — non-interactive callers (CI,
    piped input) proceed automatically so automated runs are never blocked.
    """
    if assume_yes or not stdin.isatty():
        return True
    stdout.write("Punch orchestrator will run Docker Compose (this may build images):\n")
    stdout.write(f"  {' '.join(command)}\n")
    stdout.write("Proceed? [y/N] ")
    stdout.flush()
    return stdin.readline().strip().lower() in {"y", "yes"}


def confirm_output_data(
    workflows: Sequence[K6Workflow], *, assume_yes: bool, stdin: IO[str], stdout: IO[str]
) -> bool:
    csv_workflows = [workflow for workflow in workflows if workflow.csv_output is not None]
    if not csv_workflows:
        return True

    for workflow in csv_workflows:
        assert workflow.csv_output is not None
        stdout.write(f"{workflow.name}: {workflow.csv_output.path}\n")
    stdout.flush()

    if assume_yes:
        return True
    if not stdin.isatty():
        return False

    stdout.write("Write declared CSV output? [y/N] ")
    stdout.flush()
    return stdin.readline().strip().lower() in {"y", "yes"}


def _csv_payload(line: str) -> str | None:
    record = line.rstrip("\r\n")
    if not record.startswith(CSV_TAG):
        return None
    payload = record[len(CSV_TAG) :]
    if payload.startswith(" "):
        payload = payload[1:]
    if not payload:
        raise ValueError("blank [CSV] payload")
    parsed = list(csv.reader([payload], strict=True))
    if len(parsed) != 1:
        raise ValueError("[CSV] payload must contain one record")
    return payload


def _result(
    workflow: K6Workflow,
    command: list[str],
    *,
    child_exit_code: int | None,
    passed: bool,
    failure: str | None,
    csv_path: Path | None = None,
    csv_record_count: int = 0,
) -> ExecutionResult:
    return ExecutionResult(
        workflow_name=workflow.name,
        command=tuple(command),
        child_exit_code=child_exit_code,
        passed=passed,
        failure=failure,
        csv_path=csv_path,
        csv_record_count=csv_record_count,
    )


def paths_collide(left: Path, right: Path) -> bool:
    """Return whether two output paths resolve to the same filesystem entry."""
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _read_stream(
    stream_name: str,
    stream: IO[str],
    lines: queue.Queue[tuple[str, str | Exception | None]],
) -> None:
    try:
        for line in stream:
            lines.put((stream_name, line))
    except Exception as error:
        lines.put((stream_name, error))
    finally:
        _close_stream(stream)
        lines.put((stream_name, None))


def _signal_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, sig)
            return
        except (AttributeError, OSError):
            pass
    try:
        if sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()
    except OSError:
        pass


def _process_group_exists(proc: subprocess.Popen[str]) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(proc.pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (AttributeError, OSError):
        return False


def _terminate_and_reap(proc: subprocess.Popen[str]) -> int | None:
    """Stop a child after a stream failure without allowing cleanup to hang."""
    _signal_process_group(proc, signal.SIGTERM)
    try:
        exit_code = proc.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_group(proc, signal.SIGKILL)
        try:
            exit_code = proc.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return None
    if _process_group_exists(proc):
        _signal_process_group(proc, signal.SIGKILL)
    return exit_code


def _close_stream(stream: IO[str]) -> None:
    try:
        stream.close()
    except (OSError, ValueError):
        pass


def execute_workflow(
    workflow: K6Workflow,
    *,
    environment: Mapping[str, str],
    output_data_confirmed: bool,
    docker_run_confirmed: bool = True,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    log_path: Path | None = None,
) -> ExecutionResult:
    command = build_compose_run_command(workflow, environment)
    csv_path = workflow.csv_output.path if workflow.csv_output is not None else None
    if not docker_run_confirmed:
        return _result(
            workflow,
            command,
            child_exit_code=None,
            passed=False,
            failure="Docker Compose run was not confirmed",
            csv_path=csv_path,
        )
    missing = [name for name in workflow.required_environment if not environment.get(name)]
    if missing:
        return _result(
            workflow,
            command,
            child_exit_code=None,
            passed=False,
            failure=f"missing required environment: {', '.join(missing)}",
            csv_path=csv_path,
        )
    if workflow.csv_output is not None and not output_data_confirmed:
        return _result(
            workflow,
            command,
            child_exit_code=None,
            passed=False,
            failure="CSV output requires confirmation",
            csv_path=csv_path,
        )
    if csv_path is not None and log_path is not None and paths_collide(csv_path, log_path):
        return _result(
            workflow,
            command,
            child_exit_code=None,
            passed=False,
            failure="CSV output collides with log path",
            csv_path=csv_path,
        )

    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    temp_path: Path | None = None
    csv_file: IO[str] | None = None
    log_file: IO[str] | None = None
    proc: subprocess.Popen[str] | None = None

    try:
        if csv_path is not None:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_file = NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=csv_path.parent,
                delete=False,
            )
            temp_path = Path(csv_file.name)
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("w", encoding="utf-8")

        try:
            proc = subprocess.Popen(
                command,
                cwd=workflow.working_directory,
                env=dict(environment),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as error:
            return _result(
                workflow,
                command,
                child_exit_code=None,
                passed=False,
                failure=f"could not start Docker Compose: {error}",
                csv_path=csv_path,
            )

        assert proc.stdout is not None
        assert proc.stderr is not None
        lines: queue.Queue[tuple[str, str | Exception | None]] = queue.Queue()
        readers = [
            threading.Thread(target=_read_stream, args=("stdout", proc.stdout, lines), daemon=True),
            threading.Thread(target=_read_stream, args=("stderr", proc.stderr, lines), daemon=True),
        ]
        for reader in readers:
            reader.start()

        completed_streams = 0
        csv_error: str | None = None
        reader_error: str | None = None
        csv_record_count = 0
        while completed_streams < len(readers):
            stream_name, line = lines.get()
            if line is None:
                completed_streams += 1
                continue
            if isinstance(line, Exception):
                if reader_error is None:
                    reader_error = f"could not read {stream_name}: {line}"
                break

            destination = output if stream_name == "stdout" else errors
            destination.write(line)
            destination.flush()
            if log_file is not None:
                log_file.write(line)
                log_file.flush()

            if stream_name == "stdout" and csv_file is not None and csv_error is None:
                try:
                    payload = _csv_payload(line)
                except (csv.Error, ValueError) as error:
                    csv_error = str(error)
                else:
                    if payload is not None:
                        csv_file.write(payload + "\n")
                        csv_file.flush()
                        csv_record_count += 1

        if reader_error is not None:
            child_exit_code = _terminate_and_reap(proc)
            for reader in readers:
                reader.join(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
            if any(reader.is_alive() for reader in readers):
                _signal_process_group(proc, signal.SIGKILL)
                for reader in readers:
                    reader.join(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
            if child_exit_code is not None:
                proc = None
        else:
            for reader in readers:
                reader.join()
            child_exit_code = proc.wait()
            proc = None
        if csv_file is not None:
            csv_file.close()
            csv_file = None

        if reader_error is not None:
            return _result(
                workflow,
                command,
                child_exit_code=child_exit_code,
                passed=False,
                failure=reader_error,
                csv_path=csv_path,
                csv_record_count=csv_record_count,
            )
        if child_exit_code != 0:
            return _result(
                workflow,
                command,
                child_exit_code=child_exit_code,
                passed=False,
                failure=f"Docker Compose exited with code {child_exit_code}",
                csv_path=csv_path,
                csv_record_count=csv_record_count,
            )
        if csv_error is not None:
            return _result(
                workflow,
                command,
                child_exit_code=child_exit_code,
                passed=False,
                failure=f"invalid CSV output: {csv_error}",
                csv_path=csv_path,
                csv_record_count=csv_record_count,
            )
        if csv_path is not None and csv_record_count == 0:
            return _result(
                workflow,
                command,
                child_exit_code=child_exit_code,
                passed=False,
                failure="no [CSV] stdout records were produced",
                csv_path=csv_path,
                csv_record_count=csv_record_count,
            )
        if csv_path is not None:
            assert temp_path is not None
            os.replace(temp_path, csv_path)
            temp_path = None

        return _result(
            workflow,
            command,
            child_exit_code=child_exit_code,
            passed=True,
            failure=None,
            csv_path=csv_path,
            csv_record_count=csv_record_count,
        )
    finally:
        if proc is not None:
            _terminate_and_reap(proc)
        if csv_file is not None:
            csv_file.close()
        if log_file is not None:
            log_file.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

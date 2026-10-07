from __future__ import annotations

import csv
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Callable, Mapping, Sequence

from punch.workflow import DataProduct, K6Workflow, data_env_name


DATA_TAG_PATTERN = re.compile(r"^\[DATA ([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)\] ?(.*)$")
PROCESS_STOP_TIMEOUT_SECONDS = 1.0
INTERRUPT_GRACE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class DatasetResult:
    dataset: str
    path: Path
    record_count: int
    published: bool


@dataclass(frozen=True)
class ExecutionResult:
    workflow_name: str
    command: tuple[str, ...]
    child_exit_code: int | None
    passed: bool
    failure: str | None
    datasets: tuple[DatasetResult, ...] = ()


def build_compose_run_command(
    workflow: K6Workflow,
    environment: Mapping[str, str],
    *,
    container_name: str | None = None,
    data_env: Mapping[str, str] | None = None,
) -> list[str]:
    command = [
        "docker",
        "compose",
        "-f",
        str(workflow.compose_file),
        "run",
        "--rm",
    ]
    if container_name is not None:
        command.extend(["--name", container_name])
    for name in workflow.forward_environment:
        if name in environment:
            command.extend(["-e", f"{name}={environment[name]}"])
    for name, value in (data_env or {}).items():
        command.extend(["-e", f"{name}={value}"])
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


def validate_produce(workflow: K6Workflow, produce: Sequence[str]) -> tuple[str, ...]:
    """Resolve the datasets a run opted into; "all" means every declared one."""
    declared = tuple(p.dataset for p in workflow.data.produces) if workflow.data else ()
    if "all" in produce:
        return declared
    for dataset in produce:
        if dataset not in declared:
            raise ValueError(f'workflow {workflow.name} does not produce "{dataset}"')
    return tuple(dict.fromkeys(produce))


def resolve_data_overrides(workflow: K6Workflow, raw: Sequence[str]) -> dict[str, Path]:
    """Parse `<dataset>=<path>` overrides. Paths resolve against the working
    directory and must stay beneath spec.data.directory — the only host
    directory the container can see."""
    overrides: dict[str, Path] = {}
    for item in raw:
        dataset, separator, path_text = item.partition("=")
        if not separator or not dataset or not path_text:
            raise ValueError(f"invalid --data {item!r}: expected <dataset>=<path>")
        if workflow.data is None or (
            dataset not in workflow.data.requires and dataset not in workflow.data.optional
        ):
            raise ValueError(f'workflow {workflow.name} does not require "{dataset}"')
        path = (workflow.working_directory / path_text).resolve()
        directory = workflow.data.directory
        if directory not in path.parents:
            raise ValueError(
                f"--data {dataset} path must be beneath spec.data.directory ({directory})"
            )
        overrides[dataset] = path
    return overrides


def required_data_paths(
    workflow: K6Workflow, overrides: Mapping[str, Path]
) -> dict[str, Path]:
    if workflow.data is None:
        return {}
    return {
        dataset: overrides.get(dataset, workflow.data.host_path(dataset))
        for dataset in workflow.data.requires
    }


def optional_data_paths(
    workflow: K6Workflow, overrides: Mapping[str, Path]
) -> dict[str, Path]:
    if workflow.data is None:
        return {}
    return {
        dataset: overrides.get(dataset, workflow.data.host_path(dataset))
        for dataset in workflow.data.optional
    }


def data_row_count(path: Path) -> int:
    """Non-blank rows after the header; 0 when the file is absent."""
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        next(handle, None)  # header
        return sum(1 for line in handle if line.strip())


def _has_data_rows(path: Path) -> bool:
    return data_row_count(path) > 0


def used_data_paths(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> dict[str, Path]:
    """Required datasets plus optional ones whose choice selects the file
    (no choice: the file has data rows)."""
    used = required_data_paths(workflow, overrides)
    choices = optional_choices or {}
    for dataset, path in optional_data_paths(workflow, overrides).items():
        if choices.get(dataset, _has_data_rows(path)):
            used[dataset] = path
    return used


def absent_optional_datasets(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    used = used_data_paths(workflow, overrides, optional_choices)
    return tuple(d for d in optional_data_paths(workflow, overrides) if d not in used)


def missing_datasets(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    """Datasets this run reads whose file is absent or has no data rows."""
    return tuple(
        dataset
        for dataset, path in used_data_paths(workflow, overrides, optional_choices).items()
        if not _has_data_rows(path)
    )


def preflight_requirements(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    producers_of: Callable[[str], Sequence[str]],
    optional_choices: Mapping[str, bool] | None = None,
) -> str | None:
    for dataset in missing_datasets(workflow, overrides, optional_choices):
        producers = ", ".join(producers_of(dataset)) or "no known workflow"
        return (
            f'{workflow.name} requires "{dataset}"; '
            f"produce it with: {producers} (--produce {dataset})"
        )
    return None


def data_environment(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> dict[str, str]:
    """Container paths of every used dataset, keyed DATA_<NAME>_CSV."""
    if workflow.data is None:
        return {}
    mounted_at = workflow.data.mounted_at.rstrip("/")
    return {
        data_env_name(dataset): f"{mounted_at}/{path.relative_to(workflow.data.directory).as_posix()}"
        for dataset, path in used_data_paths(workflow, overrides, optional_choices).items()
    }


DEFAULT_DATA = "default"


def resolve_data_args(
    workflow: K6Workflow, raw: Sequence[str]
) -> tuple[dict[str, Path], dict[str, bool]]:
    """Split `--data` into path overrides and `<dataset>=default` choices."""
    paths: list[str] = []
    choices: dict[str, bool] = {}
    for item in raw:
        dataset, separator, value = item.partition("=")
        if separator and value == DEFAULT_DATA:
            if workflow.data is None or dataset not in workflow.data.optional:
                raise ValueError(
                    f'--data {dataset}={DEFAULT_DATA}: "{dataset}" is not an optional '
                    f"dataset of {workflow.name}"
                )
            choices[dataset] = False
        else:
            paths.append(item)
    return resolve_data_overrides(workflow, paths), choices


def data_sources(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    optional_choices: Mapping[str, bool] | None = None,
) -> dict[str, str]:
    """Evidence: each declared input's path relative to the working directory,
    or "default" when the scenario's built-in data is used."""
    if workflow.data is None:
        return {}
    used = used_data_paths(workflow, overrides, optional_choices)
    return {
        dataset: (
            used[dataset].relative_to(workflow.working_directory).as_posix()
            if dataset in used
            else DEFAULT_DATA
        )
        for dataset in (*workflow.data.requires, *workflow.data.optional)
    }


def confirm_delete_consumed(
    paths: Mapping[str, Path], *, stdin: IO[str], stdout: IO[str]
) -> list[Path]:
    """Offer to delete consumed datasets. Only a real terminal is asked;
    non-interactive runs never delete."""
    if not stdin.isatty():
        return []
    deleted: list[Path] = []
    for dataset, path in paths.items():
        if not path.exists():
            continue
        stdout.write(f'Delete consumed "{dataset}" data ({path})? [y/N] ')
        stdout.flush()
        if stdin.readline().strip().lower() in {"y", "yes"}:
            path.unlink()
            deleted.append(path)
    return deleted


def _data_record(line: str) -> tuple[str, str] | None:
    match = DATA_TAG_PATTERN.match(line.rstrip("\r\n"))
    if match is None:
        return None
    return match.group(1), match.group(2)


def _check_payload(product: DataProduct, payload: str) -> None:
    rows = list(csv.reader([payload], strict=True))
    if len(rows) != 1:
        raise ValueError(f'"{product.dataset}" payload must contain one record')
    if len(rows[0]) != len(product.columns):
        raise ValueError(
            f'"{product.dataset}" record has {len(rows[0])} fields, '
            f"expected {len(product.columns)}"
        )


class _DatasetSink:
    """Header + records in a temp file beside the target; renamed into place
    only when the whole run succeeds."""

    def __init__(self, product: DataProduct, path: Path) -> None:
        self.product = product
        self.path = path
        self.count = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle: IO[str] | None = NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            delete=False,
            prefix=f".{product.dataset}-",
            suffix=".tmp",
        )
        self.temp_path = Path(self.handle.name)
        self.handle.write(",".join(product.columns) + "\n")

    def write(self, payload: str) -> None:
        assert self.handle is not None
        self.handle.write(payload + "\n")
        self.handle.flush()
        self.count += 1

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def publish(self) -> None:
        self.close()
        os.replace(self.temp_path, self.path)

    def discard(self) -> None:
        self.close()
        self.temp_path.unlink(missing_ok=True)

    def result(self, published: bool) -> DatasetResult:
        return DatasetResult(self.product.dataset, self.path, self.count, published)


def _result(
    workflow: K6Workflow,
    command: list[str],
    *,
    child_exit_code: int | None,
    passed: bool,
    failure: str | None,
    datasets: tuple[DatasetResult, ...] = (),
) -> ExecutionResult:
    return ExecutionResult(
        workflow_name=workflow.name,
        command=tuple(command),
        child_exit_code=child_exit_code,
        passed=passed,
        failure=failure,
        datasets=datasets,
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


def _interrupt_container(container_name: str, environment: Mapping[str, str]) -> bool:
    """Ask k6 to finish its interrupt lifecycle and emit summary outputs."""
    try:
        completed = subprocess.run(
            ["docker", "kill", "--signal=SIGINT", container_name],
            env=dict(environment),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROCESS_STOP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _close_stream(stream: IO[str]) -> None:
    try:
        stream.close()
    except (OSError, ValueError):
        pass


def execute_workflow(
    workflow: K6Workflow,
    *,
    environment: Mapping[str, str],
    produce: Sequence[str] = (),
    data_overrides: Mapping[str, Path] | None = None,
    optional_choices: Mapping[str, bool] | None = None,
    producers_of: Callable[[str], Sequence[str]] = lambda _dataset: (),
    docker_run_confirmed: bool = True,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    log_path: Path | None = None,
) -> ExecutionResult:
    overrides = dict(data_overrides or {})
    data_env = data_environment(workflow, overrides, optional_choices)
    command = build_compose_run_command(workflow, environment, data_env=data_env)

    def fail_before_start(failure: str) -> ExecutionResult:
        return _result(workflow, command, child_exit_code=None, passed=False, failure=failure)

    try:
        opted = validate_produce(workflow, produce)
    except ValueError as error:
        return fail_before_start(str(error))
    preflight_failure = preflight_requirements(
        workflow, overrides, producers_of, optional_choices
    )
    if preflight_failure is not None:
        return fail_before_start(preflight_failure)
    if not docker_run_confirmed:
        return fail_before_start("Docker Compose run was not confirmed")
    missing = [name for name in workflow.required_environment if not environment.get(name)]
    if missing:
        return fail_before_start(f"missing required environment: {', '.join(missing)}")
    if log_path is not None and workflow.data is not None:
        for dataset in opted:
            if paths_collide(workflow.data.host_path(dataset), log_path):
                return fail_before_start(f'data output "{dataset}" collides with log path')

    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    for dataset in absent_optional_datasets(workflow, overrides, optional_choices):
        output.write(
            f'[punch] optional dataset "{dataset}" not used — scenario uses its default\n'
        )
    sinks: dict[str, _DatasetSink] = {}
    log_file: IO[str] | None = None
    proc: subprocess.Popen[str] | None = None

    def finish(
        *, child_exit_code: int | None, passed: bool, failure: str | None
    ) -> ExecutionResult:
        return _result(
            workflow,
            command,
            child_exit_code=child_exit_code,
            passed=passed,
            failure=failure,
            datasets=tuple(sink.result(passed) for sink in sinks.values()),
        )

    try:
        if workflow.data is not None:
            for dataset in opted:
                product = workflow.data.product(dataset)
                assert product is not None
                sinks[dataset] = _DatasetSink(product, workflow.data.host_path(dataset))
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("w", encoding="utf-8")

        container_name = f"punch-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        command = build_compose_run_command(
            workflow, environment, container_name=container_name, data_env=data_env
        )
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
            return finish(
                child_exit_code=None,
                passed=False,
                failure=f"could not start Docker Compose: {error}",
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
        data_error: str | None = None
        reader_error: str | None = None
        interrupted = False
        interrupt_deadline: float | None = None
        interrupt_cleanup_required = False
        while completed_streams < len(readers):
            try:
                if interrupt_deadline is None:
                    stream_name, line = lines.get()
                else:
                    remaining = interrupt_deadline - time.monotonic()
                    if remaining <= 0:
                        interrupt_cleanup_required = True
                        break
                    stream_name, line = lines.get(timeout=remaining)
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

                if stream_name == "stdout" and data_error is None:
                    record = _data_record(line)
                    if record is not None:
                        dataset, payload = record
                        product = workflow.data.product(dataset) if workflow.data else None
                        if product is None:
                            data_error = f'undeclared dataset "{dataset}"'
                        elif dataset in sinks:
                            try:
                                _check_payload(product, payload)
                            except (csv.Error, ValueError) as error:
                                data_error = str(error)
                            else:
                                sinks[dataset].write(payload)
            except queue.Empty:
                interrupt_cleanup_required = True
                break
            except KeyboardInterrupt:
                if interrupted:
                    raise
                interrupted = True
                if _interrupt_container(container_name, environment):
                    interrupt_deadline = time.monotonic() + INTERRUPT_GRACE_TIMEOUT_SECONDS
                else:
                    interrupt_cleanup_required = True
                    break

        if reader_error is not None or interrupt_cleanup_required:
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
            if interrupted and _process_group_exists(proc):
                _signal_process_group(proc, signal.SIGKILL)
            proc = None
        for sink in sinks.values():
            sink.close()

        if interrupted:
            raise KeyboardInterrupt

        if reader_error is not None:
            return finish(child_exit_code=child_exit_code, passed=False, failure=reader_error)
        if child_exit_code != 0:
            return finish(
                child_exit_code=child_exit_code,
                passed=False,
                failure=f"Docker Compose exited with code {child_exit_code}",
            )
        if data_error is not None:
            return finish(
                child_exit_code=child_exit_code,
                passed=False,
                failure=f"invalid data output: {data_error}",
            )
        empty = [dataset for dataset, sink in sinks.items() if sink.count == 0]
        if empty:
            return finish(
                child_exit_code=child_exit_code,
                passed=False,
                failure=f"no [DATA {empty[0]}] records were produced",
            )
        for sink in sinks.values():
            sink.publish()
        return finish(child_exit_code=child_exit_code, passed=True, failure=None)
    finally:
        if proc is not None:
            _terminate_and_reap(proc)
        for sink in sinks.values():
            sink.discard()
        if log_file is not None:
            log_file.close()

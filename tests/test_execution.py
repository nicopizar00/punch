from __future__ import annotations

import io
import os
import shutil
import signal
import sys
import threading
import time
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.execution import (
    build_compose_run_command,
    execute_workflow,
)
from punch.workflow import load_workflow


FAKE_DOCKER = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_DOCKER_ARGS"]).write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
for line in os.environ.get("FAKE_STDOUT", "").split("|"):
    if line:
        print(line, flush=True)
for line in os.environ.get("FAKE_STDERR", "").split("|"):
    if line:
        print(line, file=sys.stderr, flush=True)
raise SystemExit(int(os.environ.get("FAKE_EXIT_CODE", "0")))
"""


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class InterruptingOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def write(self, value: str) -> int:
        if not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt
        return super().write(value)


class ExplodingStream:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.closed = False

    def __iter__(self):
        yield from self.lines
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, stdout: ExplodingStream, stderr: io.StringIO, exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.wait_called = False
        self.terminate_called = False
        self.kill_called = False

    def wait(self, timeout: float | None = None) -> int:
        self.wait_called = True
        return self.exit_code

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        fixtures = Path(__file__).resolve().parent / "fixtures"
        shutil.copy(fixtures / "docker-compose.yml", self.root / "docker-compose.yml")
        shutil.copy(fixtures / "data-output.yaml", self.root / "data-output.yaml")
        shutil.copy(fixtures / "data-input.yaml", self.root / "data-input.yaml")
        self.workflow = load_workflow(self.root / "data-output.yaml")
        self.consumer = load_workflow(self.root / "data-input.yaml")
        self.carts_path = self.root / "data" / "carts.csv"

        producer_text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        no_data = producer_text[: producer_text.index("  data:\n")]
        (self.root / "no-data.yaml").write_text(no_data, encoding="utf-8")
        self.no_csv_workflow = load_workflow(self.root / "no-data.yaml")

        self.bin_path = self.root / "bin"
        self.bin_path.mkdir()
        docker = self.bin_path / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)

        self.args_path = self.root / "docker-args.txt"
        self.log_path = self.root / "run.log"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin_path}{os.pathsep}{os.environ['PATH']}",
            "BASE_URL": "http://target",
            "RUN_ID": "run-7",
            "FAKE_DOCKER_ARGS": str(self.args_path),
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_builds_one_explicit_compose_run_with_allowlisted_environment(self) -> None:
        command = build_compose_run_command(
            self.workflow,
            {"BASE_URL": "http://target", "RUN_ID": "run-7", "SECRET": "ignored"},
        )
        self.assertEqual(command.count("run"), 2)
        self.assertEqual(
            command[:5],
            ["docker", "compose", "-f", str(self.root / "docker-compose.yml"), "run"],
        )
        self.assertNotIn("--project-directory", command)
        self.assertIn("BASE_URL=http://target", command)
        self.assertIn("RUN_ID=run-7", command)
        self.assertNotIn("SECRET=ignored", command)
        self.assertLess(command.index("BASE_URL=http://target"), command.index("RUN_ID=run-7"))
        self.assertEqual(command.count("k6"), 1)
        self.assertEqual(command[-3:], ["k6", "run", "/scripts/data-producer.js"])

    def test_missing_required_environment_fails_before_subprocess(self) -> None:
        result = execute_workflow(self.workflow, environment={}, produce=("carts",))
        self.assertFalse(result.passed)
        self.assertIn("RUN_ID", result.failure)
        self.assertFalse(self.args_path.exists())

    def run_producer(self, stdout_lines: list[str], *, produce=("carts",), exit_code: int = 0):
        env = {**self.env, "FAKE_STDOUT": "|".join(stdout_lines), "FAKE_EXIT_CODE": str(exit_code)}
        return execute_workflow(
            self.workflow,
            environment=env,
            produce=produce,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            log_path=self.log_path,
        )

    def write_previous_carts(self) -> None:
        self.carts_path.parent.mkdir(parents=True, exist_ok=True)
        self.carts_path.write_text("cartId,productId,sid\nold,old,old\n", encoding="utf-8")

    def assert_previous_carts_kept(self) -> None:
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"), "cartId,productId,sid\nold,old,old\n"
        )
        leftovers = [p.name for p in self.carts_path.parent.iterdir() if p != self.carts_path]
        self.assertEqual(leftovers, [])

    def test_opted_in_dataset_is_published_with_header(self) -> None:
        result = self.run_producer(
            ["noise", "[DATA carts] c1,p1,s1", '[DATA carts] c2,"p, 2",s2']
        )
        self.assertTrue(result.passed, result.failure)
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"),
            'cartId,productId,sid\nc1,p1,s1\nc2,"p, 2",s2\n',
        )
        self.assertEqual(len(result.datasets), 1)
        self.assertEqual(result.datasets[0].dataset, "carts")
        self.assertEqual(result.datasets[0].path, self.carts_path)
        self.assertEqual(result.datasets[0].record_count, 2)
        self.assertTrue(result.datasets[0].published)

    def test_not_opted_in_writes_nothing_and_passes(self) -> None:
        result = self.run_producer(["[DATA carts] c1,p1,s1"], produce=())
        self.assertTrue(result.passed, result.failure)
        self.assertFalse(self.carts_path.exists())
        self.assertEqual(result.datasets, ())

    def test_empty_field_counts_toward_column_count(self) -> None:
        result = self.run_producer(["[DATA carts] c1,,s1"])
        self.assertTrue(result.passed, result.failure)
        self.assertIn("c1,,s1\n", self.carts_path.read_text(encoding="utf-8"))

    def test_wrong_column_count_fails_and_preserves_old_file(self) -> None:
        self.write_previous_carts()
        result = self.run_producer(["[DATA carts] c1,p1"])
        self.assertFalse(result.passed)
        self.assertIn('"carts" record has 2 fields, expected 3', result.failure)
        self.assertFalse(result.datasets[0].published)
        self.assert_previous_carts_kept()

    def test_malformed_payload_fails_and_preserves_old_file(self) -> None:
        self.write_previous_carts()
        result = self.run_producer(["[DATA carts] c1,p1,s1", '[DATA carts] "unterminated'])
        self.assertFalse(result.passed)
        self.assertIn("invalid data output", result.failure)
        self.assertEqual(result.datasets[0].record_count, 1)
        self.assert_previous_carts_kept()

    def test_undeclared_dataset_tag_fails(self) -> None:
        result = self.run_producer(["[DATA ghosts] x"])
        self.assertFalse(result.passed)
        self.assertIn('undeclared dataset "ghosts"', result.failure)

    def test_undeclared_dataset_tag_fails_even_without_opt_in(self) -> None:
        result = self.run_producer(["[DATA ghosts] x"], produce=())
        self.assertFalse(result.passed)
        self.assertIn('undeclared dataset "ghosts"', result.failure)

    def test_opted_in_dataset_with_zero_rows_fails_and_preserves_old_file(self) -> None:
        self.write_previous_carts()
        result = self.run_producer(["no records here"])
        self.assertFalse(result.passed)
        self.assertIn("no [DATA carts] records were produced", result.failure)
        self.assert_previous_carts_kept()

    def test_child_failure_does_not_publish(self) -> None:
        self.write_previous_carts()
        result = self.run_producer(["[DATA carts] c1,p1,s1"], exit_code=17)
        self.assertFalse(result.passed)
        self.assertEqual(result.child_exit_code, 17)
        self.assertEqual(result.datasets[0].record_count, 1)
        self.assert_previous_carts_kept()

    def test_stderr_records_are_never_harvested(self) -> None:
        env = {
            **self.env,
            "FAKE_STDOUT": "[DATA carts] c1,p1,s1",
            "FAKE_STDERR": "[DATA carts] e,e,e",
        }
        result = execute_workflow(
            self.workflow, environment=env, produce=("carts",),
            stdout=io.StringIO(), stderr=io.StringIO(),
        )
        self.assertTrue(result.passed, result.failure)
        self.assertNotIn("e,e,e", self.carts_path.read_text(encoding="utf-8"))

    def test_validate_produce_expands_all_and_rejects_unknown(self) -> None:
        from punch.execution import validate_produce
        self.assertEqual(validate_produce(self.workflow, ["all"]), ("carts",))
        self.assertEqual(validate_produce(self.workflow, []), ())
        self.assertEqual(validate_produce(self.workflow, ["carts", "carts"]), ("carts",))
        with self.assertRaisesRegex(ValueError, 'data-producer does not produce "orders"'):
            validate_produce(self.workflow, ["orders"])
        with self.assertRaisesRegex(ValueError, 'does not produce "carts"'):
            validate_produce(self.no_csv_workflow, ["carts"])

    def test_unknown_produce_fails_before_subprocess(self) -> None:
        result = self.run_producer(["[DATA carts] c,p,s"], produce=("orders",))
        self.assertFalse(result.passed)
        self.assertIn('does not produce "orders"', result.failure)
        self.assertFalse(self.args_path.exists())

    def test_data_log_aliases_fail_before_writes_and_preserve_destination(self) -> None:
        self.write_previous_carts()
        aliases: list[tuple[str, Path]] = [
            ("lexical", self.carts_path.parent / "nested" / ".." / self.carts_path.name),
        ]
        symlink = self.root / "data-log-symlink"
        symlink.symlink_to(self.carts_path)
        aliases.append(("symlink", symlink))
        hardlink = self.root / "data-log-hardlink"
        os.link(self.carts_path, hardlink)
        aliases.append(("hardlink", hardlink))

        for alias_name, log_path in aliases:
            with self.subTest(alias=alias_name):
                self.args_path.unlink(missing_ok=True)
                result = execute_workflow(
                    self.workflow, environment=self.env, produce=("carts",), log_path=log_path
                )
                self.assertFalse(result.passed)
                self.assertIn("collides", result.failure)
                self.assertEqual(
                    self.carts_path.read_text(encoding="utf-8"),
                    "cartId,productId,sid\nold,old,old\n",
                )
                self.assertFalse(self.args_path.exists())

    def test_reader_error_after_data_record_fails_without_publishing(self) -> None:
        self.write_previous_carts()
        stdout = ExplodingStream(["[DATA carts] a,b,c\n"])
        stderr = io.StringIO()
        process = FakeProcess(stdout, stderr)
        with patch("punch.execution.subprocess.Popen", return_value=process):
            result = execute_workflow(self.workflow, environment=self.env, produce=("carts",))
        self.assertFalse(result.passed)
        self.assertIn("could not read stdout", result.failure)
        self.assertEqual(result.child_exit_code, 0)
        self.assertEqual(result.datasets[0].record_count, 1)
        self.assert_previous_carts_kept()
        self.assertTrue(process.wait_called)
        self.assertTrue(process.terminate_called)
        self.assertTrue(stdout.closed)
        self.assertTrue(stderr.closed)

    def write_carts(self, body: str) -> None:
        self.carts_path.parent.mkdir(parents=True, exist_ok=True)
        self.carts_path.write_text(body, encoding="utf-8")

    def run_consumer(self, overrides=None):
        return execute_workflow(
            self.consumer,
            environment=self.env,
            data_overrides=overrides or {},
            producers_of=lambda dataset: ("data-producer",),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def test_missing_required_file_fails_before_docker_and_names_producers(self) -> None:
        result = self.run_consumer()
        self.assertFalse(result.passed)
        self.assertIsNone(result.child_exit_code)
        self.assertEqual(
            result.failure,
            'data-consumer requires "carts"; produce it with: data-producer (--produce carts)',
        )
        self.assertFalse(self.args_path.exists())

    def test_missing_required_file_is_reported_even_when_docker_is_declined(self) -> None:
        result = execute_workflow(
            self.consumer, environment=self.env, docker_run_confirmed=False,
            producers_of=lambda dataset: ("data-producer",),
        )
        self.assertIn('requires "carts"', result.failure)

    def test_header_only_file_fails_preflight(self) -> None:
        self.write_carts("cartId,productId,sid\n\n")
        result = self.run_consumer()
        self.assertFalse(result.passed)
        self.assertIn('requires "carts"', result.failure)
        self.assertFalse(self.args_path.exists())

    def test_present_file_injects_container_path(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        result = self.run_consumer()
        self.assertTrue(result.passed, result.failure)
        args = self.args_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", args)
        self.assertLess(args.index("DATA_CARTS_CSV=/scripts/data/carts.csv"), args.index("k6"))

    def test_data_override_points_at_alternate_file(self) -> None:
        from punch.execution import resolve_data_overrides
        alt = self.root / "data" / "batch-2.csv"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        overrides = resolve_data_overrides(self.consumer, ["carts=data/batch-2.csv"])
        self.assertEqual(overrides, {"carts": alt})
        result = self.run_consumer(overrides)
        self.assertTrue(result.passed, result.failure)
        self.assertIn(
            "DATA_CARTS_CSV=/scripts/data/batch-2.csv",
            self.args_path.read_text(encoding="utf-8").splitlines(),
        )

    def test_data_override_outside_directory_is_rejected(self) -> None:
        from punch.execution import resolve_data_overrides
        for raw in ("carts=../x.csv", "carts=/tmp/x.csv", "carts=reports/x.csv", "carts=data"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "must be beneath spec.data.directory"):
                    resolve_data_overrides(self.consumer, [raw])

    def test_data_override_rejects_unknown_dataset_and_bad_syntax(self) -> None:
        from punch.execution import resolve_data_overrides
        with self.assertRaisesRegex(ValueError, 'data-consumer does not require "orders"'):
            resolve_data_overrides(self.consumer, ["orders=data/o.csv"])
        with self.assertRaisesRegex(ValueError, "expected <dataset>=<path>"):
            resolve_data_overrides(self.consumer, ["carts"])

    def test_delete_prompt_is_skipped_without_a_tty(self) -> None:
        from punch.execution import confirm_delete_consumed
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        out = io.StringIO()
        deleted = confirm_delete_consumed(
            {"carts": self.carts_path}, stdin=io.StringIO("y\n"), stdout=out
        )
        self.assertEqual(deleted, [])
        self.assertTrue(self.carts_path.exists())
        self.assertEqual(out.getvalue(), "")

    def test_delete_prompt_yes_deletes_on_tty(self) -> None:
        from punch.execution import confirm_delete_consumed
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        out = io.StringIO()
        deleted = confirm_delete_consumed({"carts": self.carts_path}, stdin=TtyInput("y\n"), stdout=out)
        self.assertEqual(deleted, [self.carts_path])
        self.assertFalse(self.carts_path.exists())
        self.assertIn('Delete consumed "carts" data', out.getvalue())

    def test_delete_prompt_default_keeps_file(self) -> None:
        from punch.execution import confirm_delete_consumed
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        deleted = confirm_delete_consumed(
            {"carts": self.carts_path}, stdin=TtyInput("\n"), stdout=io.StringIO()
        )
        self.assertEqual(deleted, [])
        self.assertTrue(self.carts_path.exists())

    def test_reader_decode_error_terminates_backpressured_child_without_publication(self) -> None:
        child_pid_path = self.root / "child.pid"
        descendant_pid_path = self.root / "descendant.pid"
        docker = self.bin_path / "docker"
        docker.write_text(
            """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

Path(os.environ[\"CHILD_PID_PATH\"]).write_text(str(os.getpid()), encoding=\"utf-8\")
descendant = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(30)\"])
Path(os.environ[\"DESCENDANT_PID_PATH\"]).write_text(
    str(descendant.pid), encoding=\"utf-8\"
)
sys.stdout.buffer.write(b\"[DATA carts] a,b,c\\n\")
sys.stdout.buffer.flush()
time.sleep(0.05)
sys.stdout.buffer.write(b\"\\xff\")
sys.stdout.buffer.flush()
while True:
    sys.stderr.buffer.write(b\"x\" * 65_536)
    sys.stderr.buffer.flush()
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        self.write_previous_carts()
        result: list[object] = []

        worker = threading.Thread(
            target=lambda: result.append(
                execute_workflow(
                    self.workflow,
                    environment={
                        **self.env,
                        "CHILD_PID_PATH": str(child_pid_path),
                        "DESCENDANT_PID_PATH": str(descendant_pid_path),
                    },
                    produce=("carts",),
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            )
        )
        worker.start()
        for _ in range(100):
            if child_pid_path.exists() and descendant_pid_path.exists():
                break
            time.sleep(0.01)
        self.assertTrue(child_pid_path.exists())
        self.assertTrue(descendant_pid_path.exists())
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        worker.join(timeout=0.5)
        timed_out = worker.is_alive()
        if timed_out:
            os.killpg(child_pid, signal.SIGKILL)
            worker.join(timeout=1)

        self.assertFalse(timed_out)
        self.assertEqual(len(result), 1)
        execution = result[0]
        self.assertFalse(execution.passed)
        self.assertIn("could not read stdout", execution.failure)
        self.assertEqual(execution.datasets[0].record_count, 1)
        self.assert_previous_carts_kept()
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)
        for _ in range(100):
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            self.fail("descendant process was not terminated")

    def test_output_interrupt_forwards_k6_summary_before_raising(self) -> None:
        child_pid_path = self.root / "summary-child.pid"
        container_name_path = self.root / "container-name.txt"
        kill_args_path = self.root / "summary-kill-args.txt"
        docker = self.bin_path / "docker"
        docker.write_text(
            """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

child_pid_path = Path(os.environ["CHILD_PID_PATH"])
if sys.argv[1] == "kill":
    Path(os.environ["KILL_ARGS_PATH"]).write_text(
        "\\n".join(sys.argv[1:]), encoding="utf-8"
    )
    os.kill(int(child_pid_path.read_text(encoding="utf-8")), signal.SIGINT)
    raise SystemExit(0)

container_name = sys.argv[sys.argv.index("--name") + 1]
Path(os.environ["CONTAINER_NAME_PATH"]).write_text(container_name, encoding="utf-8")
child_pid_path.write_text(str(os.getpid()), encoding="utf-8")

def handle_interrupt(_signum, _frame):
    print("K6 DEFAULT END-OF-TEST SUMMARY", flush=True)
    raise SystemExit(105)

signal.signal(signal.SIGINT, handle_interrupt)
print("ordinary output", flush=True)
time.sleep(30)
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        output = InterruptingOutput()

        with self.assertRaises(KeyboardInterrupt):
            execute_workflow(
                self.no_csv_workflow,
                environment={
                    **self.env,
                    "CHILD_PID_PATH": str(child_pid_path),
                    "CONTAINER_NAME_PATH": str(container_name_path),
                    "KILL_ARGS_PATH": str(kill_args_path),
                },
                stdout=output,
                stderr=io.StringIO(),
            )

        self.assertIn("K6 DEFAULT END-OF-TEST SUMMARY", output.getvalue())
        kill_args = kill_args_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(kill_args[:2], ["kill", "--signal=SIGINT"])
        self.assertEqual(kill_args[2], container_name_path.read_text(encoding="utf-8"))

    def test_stalled_output_interrupt_kills_term_ignoring_descendant(self) -> None:
        child_pid_path = self.root / "interrupt-child.pid"
        descendant_pid_path = self.root / "interrupt-descendant.pid"
        descendant_ready_path = self.root / "interrupt-descendant.ready"
        kill_args_path = self.root / "interrupt-kill-args.txt"
        docker = self.bin_path / "docker"
        docker.write_text(
            """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.argv[1] == "kill":
    Path(os.environ["KILL_ARGS_PATH"]).write_text(
        "\\n".join(sys.argv[1:]), encoding="utf-8"
    )
    raise SystemExit(0)

Path(os.environ[\"CHILD_PID_PATH\"]).write_text(str(os.getpid()), encoding=\"utf-8\")
descendant_code = (
    \"import signal, sys, time; from pathlib import Path; \"
    \"signal.signal(signal.SIGTERM, signal.SIG_IGN); \"
    \"Path(sys.argv[1]).write_text('ready', encoding='utf-8'); time.sleep(30)\"
)
descendant = subprocess.Popen([
    sys.executable, \"-c\", descendant_code, os.environ[\"DESCENDANT_READY_PATH\"]
])
Path(os.environ[\"DESCENDANT_PID_PATH\"]).write_text(
    str(descendant.pid), encoding=\"utf-8\"
)
while not Path(os.environ[\"DESCENDANT_READY_PATH\"]).exists():
    time.sleep(0.01)
print(\"ordinary output\", flush=True)
time.sleep(30)
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)

        with patch("punch.execution.INTERRUPT_GRACE_TIMEOUT_SECONDS", 0.05):
            with self.assertRaises(KeyboardInterrupt):
                execute_workflow(
                    self.no_csv_workflow,
                    environment={
                        **self.env,
                        "CHILD_PID_PATH": str(child_pid_path),
                        "DESCENDANT_PID_PATH": str(descendant_pid_path),
                        "DESCENDANT_READY_PATH": str(descendant_ready_path),
                        "KILL_ARGS_PATH": str(kill_args_path),
                    },
                    stdout=InterruptingOutput(),
                    stderr=io.StringIO(),
                )

        self.assertTrue(child_pid_path.exists())
        self.assertTrue(descendant_pid_path.exists())
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)
        descendant_survived = True
        for _ in range(100):
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                descendant_survived = False
                break
            time.sleep(0.01)
        if descendant_survived:
            os.kill(descendant_pid, signal.SIGKILL)
        self.assertFalse(descendant_survived)
        self.assertEqual(
            kill_args_path.read_text(encoding="utf-8").splitlines()[:2],
            ["kill", "--signal=SIGINT"],
        )

    def test_executes_one_explicit_compose_run_with_present_allowlisted_environment(self) -> None:
        self.env.pop("BASE_URL")
        self.env["SECRET"] = "ignored"
        result = execute_workflow(
            self.no_csv_workflow, environment=self.env
        )
        arguments = self.args_path.read_text(encoding="utf-8").splitlines()
        forwarded = [arguments[index + 1] for index, value in enumerate(arguments) if value == "-e"]
        name_index = arguments.index("--name")
        self.assertRegex(arguments[name_index + 1], r"^punch-\d+-[0-9a-f]{12}$")
        arguments_without_internal_name = (
            arguments[:name_index] + arguments[name_index + 2 :]
        )
        self.assertTrue(result.passed)
        self.assertEqual(arguments.count("compose"), 1)
        self.assertEqual(arguments.count("run"), 2)
        self.assertEqual(arguments.count("k6"), 1)
        self.assertEqual(
            arguments_without_internal_name,
            [
                "compose",
                "-f",
                str(self.root / "docker-compose.yml"),
                "run",
                "--rm",
                "-e",
                "RUN_ID=run-7",
                "k6",
                "run",
                "/scripts/data-producer.js",
            ],
        )
        self.assertNotIn("--project-directory", arguments)
        self.assertEqual(arguments[-3:], ["k6", "run", "/scripts/data-producer.js"])
        self.assertEqual(forwarded, ["RUN_ID=run-7"])
        self.assertNotIn("BASE_URL=http://target", arguments)
        self.assertNotIn("SECRET=ignored", arguments)

    def test_workflow_without_data_never_harvests(self) -> None:
        result = execute_workflow(self.no_csv_workflow, environment=self.env)
        self.assertTrue(result.passed)
        self.assertEqual(result.datasets, ())

    def test_closes_child_streams_after_streaming(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            result = execute_workflow(
                self.no_csv_workflow, environment=self.env
            )
        self.assertTrue(result.passed)
        self.assertEqual(
            [warning for warning in caught if issubclass(warning.category, ResourceWarning)], []
        )

    def test_streams_stdout_and_stderr_separately_and_logs_both(self) -> None:
        self.env.update(FAKE_STDOUT="stdout-line", FAKE_STDERR="stderr-line")
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = execute_workflow(
            self.no_csv_workflow,
            environment=self.env,
            stdout=stdout,
            stderr=stderr,
            log_path=self.log_path,
        )
        self.assertTrue(result.passed)
        self.assertIn("stdout-line", stdout.getvalue())
        self.assertNotIn("stderr-line", stdout.getvalue())
        self.assertIn("stderr-line", stderr.getvalue())
        self.assertIn("stdout-line", self.log_path.read_text(encoding="utf-8"))
        self.assertIn("stderr-line", self.log_path.read_text(encoding="utf-8"))

    def test_spawn_error_returns_failure_without_publishing(self) -> None:
        self.write_previous_carts()
        self.env["PATH"] = str(self.root / "missing-bin")
        result = execute_workflow(self.workflow, environment=self.env, produce=("carts",))
        self.assertFalse(result.passed)
        self.assertIsNone(result.child_exit_code)
        self.assertIn("could not start Docker Compose", result.failure)
        self.assert_previous_carts_kept()


if __name__ == "__main__":
    unittest.main()

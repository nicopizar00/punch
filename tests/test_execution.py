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
    confirm_output_data,
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
        shutil.copy(fixtures / "csv-output.yaml", self.root / "csv-output.yaml")
        self.workflow = load_workflow(self.root / "csv-output.yaml")

        no_csv = (self.root / "csv-output.yaml").read_text(encoding="utf-8").replace(
            "  outputs:\n    csv:\n      path: reports/data/fixture.csv\n", ""
        )
        (self.root / "no-csv.yaml").write_text(no_csv, encoding="utf-8")
        self.no_csv_workflow = load_workflow(self.root / "no-csv.yaml")

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
        self.assertEqual(command[-3:], ["k6", "run", "/scripts/csv-output.js"])

    def test_missing_required_environment_fails_before_subprocess(self) -> None:
        result = execute_workflow(self.workflow, environment={}, output_data_confirmed=True)
        self.assertFalse(result.passed)
        self.assertIn("RUN_ID", result.failure)
        self.assertEqual(result.csv_path, self.workflow.csv_output.path)
        self.assertFalse(self.args_path.exists())

    def test_csv_workflow_requires_confirmation_before_subprocess(self) -> None:
        result = execute_workflow(self.workflow, environment=self.env, output_data_confirmed=False)
        self.assertFalse(result.passed)
        self.assertIn("confirmation", result.failure)
        self.assertEqual(result.csv_path, self.workflow.csv_output.path)
        self.assertFalse(self.args_path.exists())

    def test_interactive_confirmation_names_every_csv_destination(self) -> None:
        output = io.StringIO()
        accepted = confirm_output_data(
            [self.workflow], assume_yes=False, stdin=TtyInput("yes\n"), stdout=output
        )
        self.assertTrue(accepted)
        self.assertIn("csv-fixture", output.getvalue())
        self.assertIn("fixture.csv", output.getvalue())

    def test_noninteractive_confirmation_requires_explicit_flag(self) -> None:
        self.assertFalse(
            confirm_output_data(
                [self.workflow], assume_yes=False, stdin=io.StringIO("yes\n"), stdout=io.StringIO()
            )
        )
        self.assertTrue(
            confirm_output_data(
                [self.workflow], assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO()
            )
        )

    def test_harvests_only_stdout_tagged_records_and_publishes_atomically(self) -> None:
        self.env["FAKE_STDOUT"] = 'ordinary|[CSV] id,name|[CSV] 7,"coffee, dark"'
        self.env["FAKE_STDERR"] = "[CSV] 99,stderr-must-not-be-data"
        result = execute_workflow(self.workflow, environment=self.env, output_data_confirmed=True)
        self.assertTrue(result.passed)
        self.assertEqual(result.csv_record_count, 2)
        self.assertEqual(
            self.workflow.csv_output.path.read_text(encoding="utf-8"), 'id,name\n7,"coffee, dark"\n'
        )

    def test_declared_csv_with_zero_tagged_lines_fails(self) -> None:
        self.workflow.csv_output.path.parent.mkdir(parents=True)
        self.workflow.csv_output.path.write_text("previous\n", encoding="utf-8")
        self.env["FAKE_STDOUT"] = "ordinary k6 output"
        result = execute_workflow(self.workflow, environment=self.env, output_data_confirmed=True)
        self.assertFalse(result.passed)
        self.assertIn("no [CSV] stdout records", result.failure)
        self.assertEqual(result.csv_path, self.workflow.csv_output.path)
        self.assertEqual(self.workflow.csv_output.path.read_text(encoding="utf-8"), "previous\n")

    def test_malformed_tagged_payload_fails_without_replacing_old_csv(self) -> None:
        self.workflow.csv_output.path.parent.mkdir(parents=True)
        self.workflow.csv_output.path.write_text("previous\n", encoding="utf-8")
        self.env["FAKE_STDOUT"] = '[CSV] id,name|[CSV] "unterminated'
        result = execute_workflow(self.workflow, environment=self.env, output_data_confirmed=True)
        self.assertFalse(result.passed)
        self.assertIn("invalid CSV output", result.failure)
        self.assertEqual(result.csv_path, self.workflow.csv_output.path)
        self.assertEqual(result.csv_record_count, 1)
        self.assertEqual(self.workflow.csv_output.path.read_text(encoding="utf-8"), "previous\n")

    def test_nonzero_child_exit_is_propagated_and_partial_csv_is_not_published(self) -> None:
        self.workflow.csv_output.path.parent.mkdir(parents=True)
        self.workflow.csv_output.path.write_text("previous\n", encoding="utf-8")
        self.env.update(FAKE_STDOUT="[CSV] id,name|[CSV] 1,espresso", FAKE_EXIT_CODE="17")
        result = execute_workflow(self.workflow, environment=self.env, output_data_confirmed=True)
        self.assertEqual(result.child_exit_code, 17)
        self.assertFalse(result.passed)
        self.assertEqual(result.csv_path, self.workflow.csv_output.path)
        self.assertEqual(result.csv_record_count, 2)
        self.assertEqual(self.workflow.csv_output.path.read_text(encoding="utf-8"), "previous\n")

    def test_csv_log_aliases_fail_before_writes_and_preserve_destination(self) -> None:
        csv_path = self.workflow.csv_output.path
        csv_path.parent.mkdir(parents=True)
        aliases: list[tuple[str, Path]] = [
            ("lexical", csv_path.parent / "nested" / ".." / csv_path.name),
        ]
        csv_path.write_text("previous\n", encoding="utf-8")
        symlink = self.root / "csv-log-symlink"
        symlink.symlink_to(csv_path)
        aliases.append(("symlink", symlink))
        hardlink = self.root / "csv-log-hardlink"
        os.link(csv_path, hardlink)
        aliases.append(("hardlink", hardlink))

        for alias_name, log_path in aliases:
            with self.subTest(alias=alias_name):
                self.args_path.unlink(missing_ok=True)
                csv_path.write_text("previous\n", encoding="utf-8")
                result = execute_workflow(
                    self.workflow,
                    environment=self.env,
                    output_data_confirmed=True,
                    log_path=log_path,
                )
                self.assertFalse(result.passed)
                self.assertIn("collides", result.failure)
                self.assertEqual(result.csv_path, csv_path)
                self.assertEqual(csv_path.read_text(encoding="utf-8"), "previous\n")
                self.assertFalse(self.args_path.exists())

    def test_csv_log_collision_preserves_destination_when_start_would_fail(self) -> None:
        csv_path = self.workflow.csv_output.path
        csv_path.parent.mkdir(parents=True)
        csv_path.write_text("previous\n", encoding="utf-8")

        with patch("punch.execution.subprocess.Popen", side_effect=OSError("not available")):
            result = execute_workflow(
                self.workflow,
                environment=self.env,
                output_data_confirmed=True,
                log_path=csv_path,
            )

        self.assertFalse(result.passed)
        self.assertIn("collides", result.failure)
        self.assertEqual(csv_path.read_text(encoding="utf-8"), "previous\n")

    def test_reader_error_after_csv_record_fails_without_publishing(self) -> None:
        self.workflow.csv_output.path.parent.mkdir(parents=True)
        self.workflow.csv_output.path.write_text("previous\n", encoding="utf-8")
        stdout = ExplodingStream(["[CSV] id,name\n"])
        stderr = io.StringIO()
        process = FakeProcess(stdout, stderr)
        with patch("punch.execution.subprocess.Popen", return_value=process):
            result = execute_workflow(
                self.workflow, environment=self.env, output_data_confirmed=True
            )
        self.assertFalse(result.passed)
        self.assertIn("could not read stdout", result.failure)
        self.assertEqual(result.child_exit_code, 0)
        self.assertEqual(result.csv_record_count, 1)
        self.assertEqual(self.workflow.csv_output.path.read_text(encoding="utf-8"), "previous\n")
        self.assertTrue(process.wait_called)
        self.assertTrue(process.terminate_called)
        self.assertTrue(stdout.closed)
        self.assertTrue(stderr.closed)

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
sys.stdout.buffer.write(b\"[CSV] id,name\\n\")
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
        self.workflow.csv_output.path.parent.mkdir(parents=True)
        self.workflow.csv_output.path.write_text("previous\n", encoding="utf-8")
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
                    output_data_confirmed=True,
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
        self.assertEqual(execution.csv_record_count, 1)
        self.assertEqual(self.workflow.csv_output.path.read_text(encoding="utf-8"), "previous\n")
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

    def test_executes_one_explicit_compose_run_with_present_allowlisted_environment(self) -> None:
        self.env.pop("BASE_URL")
        self.env["SECRET"] = "ignored"
        result = execute_workflow(
            self.no_csv_workflow, environment=self.env, output_data_confirmed=False
        )
        arguments = self.args_path.read_text(encoding="utf-8").splitlines()
        forwarded = [arguments[index + 1] for index, value in enumerate(arguments) if value == "-e"]
        self.assertTrue(result.passed)
        self.assertEqual(arguments.count("compose"), 1)
        self.assertEqual(arguments.count("run"), 2)
        self.assertEqual(arguments.count("k6"), 1)
        self.assertEqual(
            arguments,
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
                "/scripts/csv-output.js",
            ],
        )
        self.assertNotIn("--project-directory", arguments)
        self.assertEqual(arguments[-3:], ["k6", "run", "/scripts/csv-output.js"])
        self.assertEqual(forwarded, ["RUN_ID=run-7"])
        self.assertNotIn("BASE_URL=http://target", arguments)
        self.assertNotIn("SECRET=ignored", arguments)

    def test_workflow_without_csv_never_prompts_or_harvests(self) -> None:
        result = execute_workflow(self.no_csv_workflow, environment=self.env, output_data_confirmed=False)
        self.assertTrue(result.passed)
        self.assertIsNone(result.csv_path)

    def test_closes_child_streams_after_streaming(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            result = execute_workflow(
                self.no_csv_workflow, environment=self.env, output_data_confirmed=False
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
            output_data_confirmed=False,
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

    def test_spawn_error_returns_failure_without_csv(self) -> None:
        self.workflow.csv_output.path.parent.mkdir(parents=True)
        self.workflow.csv_output.path.write_text("previous\n", encoding="utf-8")
        self.env["PATH"] = str(self.root / "missing-bin")
        result = execute_workflow(self.workflow, environment=self.env, output_data_confirmed=True)
        self.assertFalse(result.passed)
        self.assertIsNone(result.child_exit_code)
        self.assertIn("could not start Docker Compose", result.failure)
        self.assertEqual(result.csv_path, self.workflow.csv_output.path)
        self.assertEqual(self.workflow.csv_output.path.read_text(encoding="utf-8"), "previous\n")


if __name__ == "__main__":
    unittest.main()

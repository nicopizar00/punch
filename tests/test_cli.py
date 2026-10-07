from __future__ import annotations

import io
import json
import os
import shutil
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.__main__ import main
from punch.workflow import load_workflow


FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = Path(os.environ["FAKE_DOCKER_ARGS"])
with arguments.open("a", encoding="utf-8") as file:
    file.write(json.dumps(sys.argv[1:]) + "\\n")

sequence_path = Path(os.environ["FAKE_DOCKER_EXIT_SEQUENCE"])
sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
exit_code = sequence.pop(0) if sequence else 0
sequence_path.write_text(json.dumps(sequence), encoding="utf-8")
for line in os.environ.get("FAKE_DOCKER_STDOUT", "").split("|"):
    if line:
        print(line, flush=True)
raise SystemExit(exit_code)
"""


PLAIN_WORKFLOW = """\
apiVersion: punch/v1
kind: K6Workflow
metadata:
  name: plain-fixture
spec:
  workingDirectory: .
  compose:
    file: docker-compose.yml
    service: k6
  k6:
    script: /scripts/plain-fixture.js
"""


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class SelectedMenu:
    def __init__(self, index: int | None) -> None:
        self.index = index

    def show(self) -> int | None:
        return self.index


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temporary_directory.name)
        self.state_dir = self.root / "state"
        self.logs_dir = self.root / "logs"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.args_path = self.root / "docker-arguments.jsonl"
        self.exit_sequence_path = self.root / "docker-exit-sequence.json"
        self.configure_fake_exit_sequence([])

        docker = self.bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)

        fixtures = Path(__file__).resolve().parent / "fixtures"
        shutil.copy(fixtures / "docker-compose.yml", self.root / "docker-compose.yml")
        self.workflow_path = self.root / "workflow.yaml"
        self.workflow_path.write_text(PLAIN_WORKFLOW, encoding="utf-8")

        # A separate directory is one catalog: producer + consumer of "carts".
        self.flows = self.root / "flows"
        self.flows.mkdir()
        shutil.copy(fixtures / "docker-compose.yml", self.flows / "docker-compose.yml")
        shutil.copy(fixtures / "data-output.yaml", self.flows / "data-output.yaml")
        shutil.copy(fixtures / "data-input.yaml", self.flows / "data-input.yaml")
        self.producer_path = self.flows / "data-output.yaml"
        self.consumer_path = self.flows / "data-input.yaml"
        self.carts_path = self.flows / "data" / "carts.csv"

        self.environment = {
            **os.environ,
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_ARGS": str(self.args_path),
            "FAKE_DOCKER_EXIT_SEQUENCE": str(self.exit_sequence_path),
        }
        self.module_patch = patch.multiple(
            "punch.__main__", STATE_DIR=self.state_dir, LOGS_DIR=self.logs_dir
        )
        self.module_patch.start()
        self.environment_patch = patch.dict(os.environ, self.environment, clear=True)
        self.environment_patch.start()
        self.stdin_patch = patch("sys.stdin", io.StringIO())
        self.stdin_patch.start()

    def tearDown(self) -> None:
        self.stdin_patch.stop()
        self.environment_patch.stop()
        self.module_patch.stop()
        self.temporary_directory.cleanup()

    def configure_fake_exit_sequence(self, exit_codes: list[int]) -> None:
        self.exit_sequence_path.write_text(json.dumps(exit_codes), encoding="utf-8")

    def evidence(self) -> dict:
        return json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))

    def fake_docker_arguments(self) -> list[str]:
        if not self.args_path.exists():
            return []
        return [argument for line in self.args_path.read_text(encoding="utf-8").splitlines() for argument in json.loads(line)]

    def compose_run_count(self) -> int:
        return sum(arguments.count("run") == 2 for arguments in self.fake_docker_calls())

    def fake_docker_calls(self) -> list[list[str]]:
        if not self.args_path.exists():
            return []
        return [json.loads(line) for line in self.args_path.read_text(encoding="utf-8").splitlines()]

    def test_named_selector_resolves_bundled_yaml_and_runs_once(self) -> None:
        rc = main(["run", "smoke"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.compose_run_count(), 1)
        self.assertNotIn("build", self.fake_docker_arguments())

    def test_direct_yaml_path_is_accepted(self) -> None:
        rc = main(["run", str(self.workflow_path)])
        self.assertEqual(rc, 0)

    def test_produce_flag_publishes_dataset_noninteractively(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        rc = main(["run", str(self.producer_path), "--produce", "carts"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.compose_run_count(), 1)
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"), "cartId,productId,sid\nc,p,s\n"
        )
        self.assertEqual(
            self.evidence()["results"][0]["datasets"],
            [{"dataset": "carts", "path": "data/carts.csv", "recordCount": 1, "published": True}],
        )

    def test_run_without_produce_writes_no_dataset(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        rc = main(["run", str(self.producer_path)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.carts_path.exists())
        self.assertEqual(self.evidence()["results"][0]["datasets"], [])

    def test_produce_without_records_fails_with_punch_exit_code(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        rc = main(["run", str(self.producer_path), "--produce", "all"])
        self.assertEqual(rc, 1)
        result = self.evidence()["results"][0]
        self.assertEqual(result["childExitCode"], 0)
        self.assertIn("no [DATA carts] records", result["failure"])
        self.assertFalse(self.carts_path.exists())

    def test_unknown_produce_dataset_fails_before_docker(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        rc = main(["run", str(self.producer_path), "--produce", "orders"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertIn('does not produce "orders"', self.evidence()["results"][0]["failure"])

    def test_evidence_distinguishes_nonzero_child_exit_code(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        self.configure_fake_exit_sequence([17])
        rc = main(["run", str(self.producer_path), "--produce", "carts"])
        self.assertEqual(rc, 17)
        result = self.evidence()["results"][0]
        self.assertEqual(result["exitCode"], 17)
        self.assertEqual(result["childExitCode"], 17)

    def test_consumer_without_data_names_producer_before_docker(self) -> None:
        rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertIn(
            "produce it with: data-producer (--produce carts)",
            self.evidence()["results"][0]["failure"],
        )

    def test_consumer_with_data_gets_container_path(self) -> None:
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", self.fake_docker_arguments())
        self.assertTrue(self.carts_path.exists())  # non-interactive never deletes

    def test_data_override_flag_is_forwarded(self) -> None:
        alt = self.flows / "data" / "alt.csv"
        alt.parent.mkdir(parents=True)
        alt.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        rc = main(["run", str(self.consumer_path), "--data", "carts=data/alt.csv"])
        self.assertEqual(rc, 0)
        self.assertIn("DATA_CARTS_CSV=/scripts/data/alt.csv", self.fake_docker_arguments())

    def test_data_override_outside_directory_fails_before_docker(self) -> None:
        rc = main(["run", str(self.consumer_path), "--data", "carts=../x.csv"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)

    def test_catalog_error_fails_before_docker(self) -> None:
        text = self.producer_path.read_text(encoding="utf-8")
        self.producer_path.write_text(text.replace("[data-consumer]", "[ghost]"), encoding="utf-8")
        rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertIn('targets unknown workflow "ghost"', self.evidence()["results"][0]["failure"])

    def test_dataset_aliasing_the_state_artifact_is_rejected_before_writing(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        state_path = self.state_dir / "punch-run.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("previous state\n", encoding="utf-8")
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.symlink_to(state_path)
        rc = main(["run", str(self.producer_path), "--produce", "carts"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(state_path.read_text(encoding="utf-8"), "previous state\n")

    def test_produce_and_data_are_rejected_with_all(self) -> None:
        self.assertEqual(main(["run", "all", "--produce", "carts"]), 1)
        self.assertEqual(main(["run", "all", "--data", "carts=data/x.csv"]), 1)
        self.assertEqual(self.compose_run_count(), 0)

    def test_confirm_output_data_flag_is_gone(self) -> None:
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            main(["run", "smoke", "--confirm-output-data"])

    def test_direct_external_workflow_requires_target_before_run(self) -> None:
        rc = main(["run", "bff-checkout-journey"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)

    def test_direct_external_workflow_rejects_an_empty_target_before_run(self) -> None:
        os.environ["TARGET_BASE_URL"] = ""
        rc = main(["run", "bff-checkout-journey"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)

    def test_all_skips_external_workflow_without_target(self) -> None:
        rc = main(["run", "all"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.compose_run_count(), 3)

    def test_keep_going_and_child_exit_code_behavior_is_preserved(self) -> None:
        self.configure_fake_exit_sequence([0, 9, 0])
        rc = main(["run", "all", "--keep-going"])
        self.assertEqual(rc, 9)
        self.assertEqual(self.compose_run_count(), 3)

    def test_evidence_records_workflow_and_dataset_fields(self) -> None:
        self.assertEqual(main(["run", "smoke"]), 0)
        result = self.evidence()["results"][0]
        self.assertIn("workflow", result)
        self.assertEqual(result["datasets"], [])
        self.assertNotIn("csvRecordCount", result)

    @contextmanager
    def picker(self, *indices: int | None):
        calls: list[tuple[list[str], dict]] = []
        selections = iter(indices)

        def factory(entries, **kwargs):
            calls.append((list(entries), kwargs))
            return SelectedMenu(next(selections))

        output = io.StringIO()
        with patch("sys.stdin", TtyInput("")), patch("sys.stdout", output):
            with patch("punch.menu.TerminalMenu", side_effect=factory):
                yield calls, output

    def add_recommended_browser_producer(self) -> None:
        text = self.producer_path.read_text(encoding="utf-8")
        (self.flows / "data-browser.yaml").write_text(
            text.replace("name: data-producer", "name: data-browser")
                .replace("/scripts/data-producer.js", "/scripts/data-browser.js"),
            encoding="utf-8",
        )
        self.producer_path.write_text(
            text.replace("targets: [data-consumer]",
                         "targets: [data-consumer]\n        recommended: true"),
            encoding="utf-8",
        )

    def make_consumer_optional(self) -> None:
        text = self.consumer_path.read_text(encoding="utf-8")
        self.consumer_path.write_text(
            text.replace("requires: [carts]", "optional: [carts]"), encoding="utf-8"
        )

    def write_carts_rows(self) -> None:
        self.carts_path.parent.mkdir(parents=True, exist_ok=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")

    def test_producer_picker_switch_runs_producer_once(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        self.add_recommended_browser_producer()
        with self.picker(1) as (calls, output):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["data-browser", "data-producer  (recommended)"])
        self.assertEqual(kwargs["cursor_index"], 1)
        self.assertIn('"data-consumer" needs "carts"', kwargs["title"])
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/data-producer.js", call)
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"), "cartId,productId,sid\nc,p,s\n"
        )
        evidence = self.evidence()
        self.assertEqual(evidence["tests"], ["data-producer"])
        self.assertEqual(evidence["results"][0]["switchedFrom"], ["data-consumer"])
        self.assertIn("[punch] carts ready; run data-consumer next.", output.getvalue())

    def test_arrow_keys_can_pick_a_non_recommended_producer(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        self.add_recommended_browser_producer()
        with self.picker(0):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/data-browser.js", call)

    def test_cancelled_producer_picker_fails_with_reason(self) -> None:
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertTrue(result["failure"].startswith("data selection canceled;"))
        self.assertIn("produce it with: data-producer (--produce carts)", result["failure"])
        self.assertNotIn("switchedFrom", result)

    def test_optional_source_default_runs_without_data_env(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker(0) as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [(entries, kwargs)] = calls
        self.assertEqual(entries, ["default (built-in)", "data/carts.csv (1 rows)"])
        self.assertEqual(kwargs["cursor_index"], 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))
        self.assertEqual(self.evidence()["results"][0]["dataSources"], {"carts": "default"})

    def test_optional_source_file_injects_data_env(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker(1):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", call)
        self.assertEqual(
            self.evidence()["results"][0]["dataSources"], {"carts": "data/carts.csv"}
        )

    def test_cancelled_source_picker_fails_before_docker(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker(None):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(self.evidence()["results"][0]["failure"], "data selection canceled")

    def test_no_input_in_tty_opens_no_picker(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path), "--no-input"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        [call] = self.fake_docker_calls()
        self.assertIn("DATA_CARTS_CSV=/scripts/data/carts.csv", call)

    def test_data_default_skips_picker_in_tty(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path), "--data", "carts=default"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_data_default_applies_without_tty(self) -> None:
        self.make_consumer_optional()
        self.write_carts_rows()
        rc = main(["run", str(self.consumer_path), "--data", "carts=default"])
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_data_default_on_required_fails_before_docker(self) -> None:
        rc = main(["run", str(self.consumer_path), "--data", "carts=default"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertIn("is not an optional dataset", self.evidence()["results"][0]["failure"])

    def test_switch_drops_consumer_flags_with_note(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        empty = self.flows / "data" / "empty.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("cartId,productId,sid\n", encoding="utf-8")
        with self.picker(0) as (_, output):
            rc = main(["run", str(self.consumer_path), "--data", "carts=data/empty.csv"])
        self.assertEqual(rc, 0)
        self.assertIn("[punch] ignoring --produce/--data for data-consumer", output.getvalue())
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_switched_producer_missing_env_reports_switch(self) -> None:
        with self.picker(0) as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(calls[0][0], ["data-producer  (needs RUN_ID)"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("missing required environment: RUN_ID", result["failure"])
        self.assertEqual(result["switchedFrom"], ["data-consumer"])

    def test_present_required_data_opens_no_picker(self) -> None:
        self.write_carts_rows()
        with self.picker() as (calls, _):
            rc = main(["run", str(self.consumer_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        result = self.evidence()["results"][0]
        self.assertNotIn("switchedFrom", result)
        self.assertEqual(result["dataSources"], {"carts": "data/carts.csv"})

    def test_workflow_without_data_records_no_data_sources(self) -> None:
        self.assertEqual(main(["run", str(self.workflow_path)]), 0)
        self.assertNotIn("dataSources", self.evidence()["results"][0])

    def test_non_tty_never_plans(self) -> None:
        with patch("punch.__main__.plan_data") as plan:
            main(["run", str(self.consumer_path)])
        plan.assert_not_called()

    def test_all_selector_never_plans(self) -> None:
        with patch("sys.stdin", TtyInput("")), patch("punch.__main__.plan_data") as plan:
            main(["run", "all"])
        plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()

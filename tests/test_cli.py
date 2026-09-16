from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
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


CSV_WORKFLOW = """\
apiVersion: punch/v1
kind: K6Workflow
metadata:
  name: csv-fixture
spec:
  workingDirectory: .
  compose:
    file: docker-compose.yml
    service: k6
  k6:
    script: /scripts/csv-fixture.js
  outputs:
    csv:
      path: reports/data/fixture.csv
"""


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

        shutil.copy(Path(__file__).resolve().parent / "fixtures" / "docker-compose.yml", self.root / "docker-compose.yml")
        self.workflow_path = self.root / "workflow.yaml"
        self.workflow_path.write_text(CSV_WORKFLOW.replace("  outputs:\n    csv:\n      path: reports/data/fixture.csv\n", ""), encoding="utf-8")
        self.csv_workflow_path = self.root / "csv-workflow.yaml"
        self.csv_workflow_path.write_text(CSV_WORKFLOW, encoding="utf-8")

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

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.module_patch.stop()
        self.temporary_directory.cleanup()

    def configure_fake_exit_sequence(self, exit_codes: list[int]) -> None:
        self.exit_sequence_path.write_text(json.dumps(exit_codes), encoding="utf-8")

    def write_csv_workflow(self, output_path: str) -> None:
        self.csv_workflow_path.write_text(
            CSV_WORKFLOW.replace("reports/data/fixture.csv", output_path), encoding="utf-8"
        )

    def load_workflow(self, name: str, output_path: str | None = None):
        path = self.root / f"{name}.yaml"
        workflow = CSV_WORKFLOW.replace("csv-fixture", name)
        if output_path is None:
            workflow = workflow.replace("  outputs:\n    csv:\n      path: reports/data/fixture.csv\n", "")
        else:
            workflow = workflow.replace("reports/data/fixture.csv", output_path)
        path.write_text(workflow, encoding="utf-8")
        return load_workflow(path)

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

    def test_csv_path_refuses_noninteractive_run_without_flag(self) -> None:
        rc = main(["run", str(self.csv_workflow_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        record = json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))
        result = record["results"][0]
        self.assertFalse(result["passed"])
        self.assertEqual(result["exitCode"], 1)
        self.assertIsNone(result["childExitCode"])
        self.assertEqual(result["workflow"], "csv-workflow.yaml")
        self.assertEqual(result["csvPath"], "reports/data/fixture.csv")
        self.assertIn("confirmation", result["failure"])

    def test_confirm_output_data_allows_noninteractive_csv_run(self) -> None:
        os.environ["FAKE_DOCKER_STDOUT"] = "[CSV] id,name|[CSV] 1,espresso"
        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.compose_run_count(), 1)
        record = json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))
        result = record["results"][0]
        self.assertTrue(result["passed"])
        self.assertEqual(result["csvRecordCount"], 2)
        self.assertEqual(result["csvPath"], "reports/data/fixture.csv")
        self.assertEqual(
            (self.root / "reports/data/fixture.csv").read_text(encoding="utf-8"),
            "id,name\n1,espresso\n",
        )

    def test_csv_without_tagged_records_fails_with_punch_exit_code(self) -> None:
        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data"])
        self.assertEqual(rc, 1)
        record = json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))
        result = record["results"][0]
        self.assertFalse(result["passed"])
        self.assertEqual(result["exitCode"], 1)
        self.assertEqual(result["childExitCode"], 0)
        self.assertIn("no [CSV] stdout records", result["failure"])
        self.assertFalse((self.root / "reports/data/fixture.csv").exists())

    def test_invalid_csv_fails_with_punch_exit_code(self) -> None:
        os.environ["FAKE_DOCKER_STDOUT"] = '[CSV] "unterminated'
        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data"])
        self.assertEqual(rc, 1)
        record = json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))
        result = record["results"][0]
        self.assertFalse(result["passed"])
        self.assertEqual(result["exitCode"], 1)
        self.assertIn("invalid CSV output", result["failure"])

    def test_evidence_distinguishes_nonzero_child_exit_code(self) -> None:
        self.configure_fake_exit_sequence([17])
        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data"])
        self.assertEqual(rc, 17)
        result = json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))["results"][0]
        self.assertEqual(result["exitCode"], 17)
        self.assertEqual(result["childExitCode"], 17)

    def test_csv_hardlink_to_state_artifact_is_rejected_before_writing(self) -> None:
        state_path = self.state_dir / "punch-run.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("previous state\n", encoding="utf-8")
        alias_path = self.root / "reports" / "data" / "state-alias.csv"
        alias_path.parent.mkdir(parents=True)
        os.link(state_path, alias_path)
        self.write_csv_workflow("reports/data/state-alias.csv")

        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data"])

        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(state_path.read_text(encoding="utf-8"), "previous state\n")

    def test_csv_symlink_to_workflow_log_is_rejected_before_writing(self) -> None:
        log_path = self.logs_dir / "k6-csv-fixture.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("previous log\n", encoding="utf-8")
        alias_path = self.root / "reports" / "data" / "log-alias.csv"
        alias_path.parent.mkdir(parents=True)
        alias_path.symlink_to(log_path)
        self.write_csv_workflow("reports/data/log-alias.csv")

        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data"])

        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "previous log\n")
        self.assertFalse((self.state_dir / "punch-run.json").exists())

    def test_csv_collision_with_another_selected_workflow_log_is_rejected(self) -> None:
        log_path = self.logs_dir / "k6-second-fixture.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("previous second log\n", encoding="utf-8")
        self.write_csv_workflow("logs/k6-second-fixture.log")
        workflows = [
            load_workflow(self.csv_workflow_path),
            self.load_workflow("second-fixture"),
        ]

        with patch("punch.__main__._load_selected_workflows", return_value=workflows):
            rc = main(["run", "all", "--confirm-output-data"])

        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "previous second log\n")

    def test_csv_collision_with_collected_service_log_is_rejected(self) -> None:
        log_path = self.logs_dir / "gateway-api.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("previous service log\n", encoding="utf-8")
        self.write_csv_workflow("logs/gateway-api.log")

        rc = main(["run", str(self.csv_workflow_path), "--confirm-output-data", "--collect-logs"])

        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "previous service log\n")

    def test_selected_csv_destinations_that_alias_are_rejected(self) -> None:
        destination = self.root / "reports" / "data" / "shared.csv"
        destination.parent.mkdir(parents=True)
        destination.write_text("previous csv\n", encoding="utf-8")
        self.write_csv_workflow("reports/data/shared.csv")
        workflows = [
            load_workflow(self.csv_workflow_path),
            self.load_workflow("second-fixture", "reports/data/shared.csv"),
        ]

        with patch("punch.__main__._load_selected_workflows", return_value=workflows):
            rc = main(["run", "all", "--confirm-output-data"])

        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "previous csv\n")

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

    def test_evidence_records_workflow_and_csv_fields(self) -> None:
        self.assertEqual(main(["run", "smoke"]), 0)
        record = json.loads((self.state_dir / "punch-run.json").read_text(encoding="utf-8"))
        self.assertIn("workflow", record["results"][0])
        self.assertIn("csvRecordCount", record["results"][0])


if __name__ == "__main__":
    unittest.main()

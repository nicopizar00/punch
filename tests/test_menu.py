from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.menu import discover_workflows, run_menu


FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = Path(os.environ["FAKE_DOCKER_ARGS"])
with arguments.open("a", encoding="utf-8") as file:
    file.write(json.dumps(sys.argv[1:]) + "\\n")

for line in os.environ.get("FAKE_DOCKER_STDOUT", "").split("|"):
    if line:
        print(line, flush=True)
raise SystemExit(int(os.environ.get("FAKE_DOCKER_EXIT_CODE", "0")))
"""

WORKFLOW_TEMPLATE = """\
apiVersion: punch/v1
kind: K6Workflow
metadata:
  name: {name}
spec:
  workingDirectory: .
  compose:
    file: docker-compose.yml
    service: k6
  k6:
    script: /scripts/{name}.js
{environment}{outputs}
"""


class MenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temporary_directory.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.args_path = self.root / "docker-arguments.jsonl"

        docker = self.bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)

        import shutil

        shutil.copy(
            Path(__file__).resolve().parent / "fixtures" / "docker-compose.yml",
            self.root / "docker-compose.yml",
        )

        self.environment_patch = patch.dict(
            os.environ,
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_DOCKER_ARGS": str(self.args_path),
            },
        )
        self.environment_patch.start()

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.temporary_directory.cleanup()

    def write_workflow(
        self, name: str, *, forward: list[str] | None = None, csv_path: str | None = None
    ) -> Path:
        environment = ""
        if forward:
            environment = "  environment:\n    forward: [" + ", ".join(forward) + "]\n"
        outputs = ""
        if csv_path:
            outputs = f"  outputs:\n    csv:\n      path: {csv_path}\n"
        path = self.root / f"{name}.yaml"
        path.write_text(
            WORKFLOW_TEMPLATE.format(name=name, environment=environment, outputs=outputs),
            encoding="utf-8",
        )
        return path

    def fake_docker_calls(self) -> list[list[str]]:
        import json

        if not self.args_path.exists():
            return []
        return [json.loads(line) for line in self.args_path.read_text(encoding="utf-8").splitlines()]

    def test_discover_workflows_lists_yaml_files_sorted(self) -> None:
        self.write_workflow("beta")
        self.write_workflow("alpha")
        self.assertEqual(
            [path.stem for path in discover_workflows(self.root)], ["alpha", "beta"]
        )

    def test_no_workflows_reports_and_returns_1(self) -> None:
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        with patch("builtins.input", side_effect=["1"]):
            rc = run_menu(empty_dir)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_run_menu_executes_selected_workflow_once(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        with patch("builtins.input", side_effect=["1", "1", "1"]):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake_docker_calls()), 1)

    def test_custom_base_url_is_forwarded_to_compose_run(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        with patch("builtins.input", side_effect=["1", "1", "2", "http://example.invalid"]):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("BASE_URL=http://example.invalid", call)

    def test_csv_workflow_prompts_for_confirmation(self) -> None:
        self.write_workflow("fixture", csv_path="reports/data/fixture.csv")
        with patch("builtins.input", side_effect=["1", "1", "n"]):
            rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_confirming_csv_workflow_runs_and_writes_output(self) -> None:
        self.write_workflow("fixture", csv_path="reports/data/fixture.csv")
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[CSV] id|[CSV] 1"}):
            with patch("builtins.input", side_effect=["1", "1", "y"]):
                rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(
            (self.root / "reports/data/fixture.csv").read_text(encoding="utf-8"), "id\n1\n"
        )

    def test_monitoring_setup_is_a_stub(self) -> None:
        self.write_workflow("fixture")
        with patch("builtins.input", side_effect=["2"]):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])


if __name__ == "__main__":
    unittest.main()

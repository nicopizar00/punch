from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.menu import discover_workflows, run_menu

DEFAULT_BASE_URL = "http://host.docker.internal:3001"
DEFAULT_BROWSER_BASE_URL = "http://host.docker.internal:3000"


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
    service: {service}
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
        self,
        name: str,
        *,
        forward: list[str] | None = None,
        csv_path: str | None = None,
        service: str = "k6",
    ) -> Path:
        environment = ""
        if forward:
            environment = "  environment:\n    forward: [" + ", ".join(forward) + "]\n"
        outputs = ""
        if csv_path:
            outputs = f"  outputs:\n    csv:\n      path: {csv_path}\n"
        path = self.root / f"{name}.yaml"
        path.write_text(
            WORKFLOW_TEMPLATE.format(
                name=name, service=service, environment=environment, outputs=outputs
            ),
            encoding="utf-8",
        )
        return path

    def write_options(self, name: str, content: dict | str) -> Path:
        options_dir = self.root_options_dir()
        options_dir.mkdir(exist_ok=True)
        path = options_dir / f"{name}.json"
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def root_options_dir(self) -> Path:
        return self.root / "options"

    def fake_docker_calls(self) -> list[list[str]]:
        if not self.args_path.exists():
            return []
        return [json.loads(line) for line in self.args_path.read_text(encoding="utf-8").splitlines()]

    @contextmanager
    def select_menu(self, *indices: int | None):
        """Supply terminal selections while retaining the real workflow/execution path."""
        with patch("sys.stdin", _ConfirmedTerminal()):
            with patch(
                "punch.menu.TerminalMenu",
                side_effect=[_SelectedMenu(index) for index in indices],
            ):
                yield

    def test_discover_workflows_lists_yaml_files_sorted(self) -> None:
        self.write_workflow("beta")
        self.write_workflow("alpha")
        self.assertEqual(
            [path.stem for path in discover_workflows(self.root)], ["alpha", "beta"]
        )

    def test_no_workflows_reports_and_returns_1(self) -> None:
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        with self.select_menu(0):
            rc = run_menu(empty_dir)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_run_menu_executes_selected_workflow_once(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        with self.select_menu(0, 0, 0):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake_docker_calls()), 1)

    def test_custom_base_url_is_forwarded_to_compose_run(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        with self.select_menu(0, 0, 1):
            with patch("builtins.input", return_value="http://example.invalid"):
                rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("BASE_URL=http://example.invalid", call)

    def test_csv_workflow_prompts_for_confirmation(self) -> None:
        self.write_workflow("fixture", csv_path="reports/data/fixture.csv")
        with self.select_menu(0, 0):
            with patch("builtins.input", return_value="n"):
                rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_confirming_csv_workflow_runs_and_writes_output(self) -> None:
        self.write_workflow("fixture", csv_path="reports/data/fixture.csv")
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[CSV] id|[CSV] 1"}):
            with self.select_menu(0, 0):
                with patch("builtins.input", return_value="y"):
                    rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(
            (self.root / "reports/data/fixture.csv").read_text(encoding="utf-8"), "id\n1\n"
        )

    def test_current_target_defaults_to_docker_host_json_when_base_url_unset(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        os.environ.pop("BASE_URL", None)
        with self.select_menu(0, 0, 0):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn(f"BASE_URL={DEFAULT_BASE_URL}", call)

    def test_env_base_url_takes_precedence_over_docker_host_json(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        with patch.dict(os.environ, {"BASE_URL": "http://current.invalid"}):
            with self.select_menu(0, 0, 0):
                rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("BASE_URL=http://current.invalid", call)

    def test_current_target_defaults_to_a_service_specific_host_json(self) -> None:
        """A workflow on a non-'k6' compose service (e.g. a browser variant)
        must not default to docker-host.json's BASE_URL — that file is only
        ever a valid target for the plain 'k6' service."""
        self.write_workflow("fixture", forward=["BASE_URL"], service="k6-browser")
        os.environ.pop("BASE_URL", None)
        with self.select_menu(0, 0, 0):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn(f"BASE_URL={DEFAULT_BROWSER_BASE_URL}", call)

    def test_current_target_falls_back_to_docker_host_json_for_unknown_service(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"], service="k6-otel")
        os.environ.pop("BASE_URL", None)
        with self.select_menu(0, 0, 0):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn(f"BASE_URL={DEFAULT_BASE_URL}", call)

    def test_monitoring_setup_is_a_stub(self) -> None:
        self.write_workflow("fixture")
        with self.select_menu(1):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_second_workflow_is_selected_from_sorted_menu(self) -> None:
        self.write_workflow("alpha")
        self.write_workflow("beta")
        with self.select_menu(0, 1):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        calls = self.fake_docker_calls()
        self.assertEqual(len(calls), 1)
        [call] = calls
        self.assertIn("/scripts/beta.js", call)

    def test_canceling_top_level_menu_does_not_run_workflow(self) -> None:
        self.write_workflow("fixture")
        with self.select_menu(None):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_canceling_workflow_menu_does_not_run_workflow(self) -> None:
        self.write_workflow("fixture")
        with self.select_menu(0, None):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_canceling_target_menu_does_not_run_workflow(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        with self.select_menu(0, 0, None):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_menu_without_terminal_fails_cleanly_before_docker(self) -> None:
        self.write_workflow("fixture")
        with patch("sys.stdin", _ConfirmedTerminal()):
            with patch("punch.menu.TerminalMenu") as menu_class:
                menu_class.return_value.show.side_effect = OSError("no terminal")
                rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_unsupported_terminal_fails_cleanly_before_docker(self) -> None:
        self.write_workflow("fixture")
        with patch("sys.stdin", _ConfirmedTerminal()):
            with patch("punch.menu.TerminalMenu", side_effect=NotImplementedError("TERM unset")):
                rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_redirected_stdin_cannot_skip_docker_confirmation(self) -> None:
        self.write_workflow("fixture")
        with patch("sys.stdin", io.StringIO()):
            with patch("punch.menu.TerminalMenu", side_effect=[_SelectedMenu(0), _SelectedMenu(0)]):
                rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_discover_options_lists_json_files_sorted(self) -> None:
        self.write_options("b-preset", {"VUS": 2})
        self.write_options("a-preset", {"VUS": 1})
        from punch.menu import discover_options

        self.assertEqual(
            [path.stem for path in discover_options(self.root_options_dir())],
            ["a-preset", "b-preset"],
        )

    def test_options_preset_is_forwarded_to_compose_run(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL", "VUS"])
        self.write_options("5-vus", {"VUS": 5})
        with self.select_menu(0, 0, 0, 1):
            rc = run_menu(self.root, options_dir=self.root_options_dir())
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("VUS=5", call)

    def test_skipping_options_preset_leaves_var_unforwarded(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL", "VUS"])
        self.write_options("5-vus", {"VUS": 5})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VUS", None)
            with self.select_menu(0, 0, 0, 0):
                rc = run_menu(self.root, options_dir=self.root_options_dir())
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(part.startswith("VUS=") for part in call))

    def test_missing_options_dir_skips_step(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL", "VUS"])
        with self.select_menu(0, 0, 0):
            rc = run_menu(self.root, options_dir=self.root_options_dir())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake_docker_calls()), 1)

    def test_workflow_without_extra_forward_vars_skips_options_step(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL"])
        self.write_options("5-vus", {"VUS": 5})
        with self.select_menu(0, 0, 0):
            rc = run_menu(self.root, options_dir=self.root_options_dir())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake_docker_calls()), 1)

    def test_canceling_options_menu_does_not_run_workflow(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL", "VUS"])
        self.write_options("5-vus", {"VUS": 5})
        with self.select_menu(0, 0, 0, None):
            rc = run_menu(self.root, options_dir=self.root_options_dir())
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake_docker_calls(), [])

    def test_malformed_options_json_warns_and_skips_merge(self) -> None:
        self.write_workflow("fixture", forward=["BASE_URL", "VUS"])
        self.write_options("broken", "not json")
        with self.select_menu(0, 0, 0, 1):
            rc = run_menu(self.root, options_dir=self.root_options_dir())
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(part.startswith("VUS=") for part in call))

    def test_default_options_dir_is_sibling_of_workflows_dir(self) -> None:
        workflows_dir = self.root / "workflows"
        workflows_dir.mkdir()
        import shutil

        shutil.copy(self.root / "docker-compose.yml", workflows_dir / "docker-compose.yml")
        (workflows_dir / "fixture.yaml").write_text(
            WORKFLOW_TEMPLATE.format(
                name="fixture",
                service="k6",
                environment="  environment:\n    forward: [BASE_URL, VUS]\n",
                outputs="",
            ),
            encoding="utf-8",
        )
        options_dir = self.root / "options"
        options_dir.mkdir()
        (options_dir / "5-vus.json").write_text(json.dumps({"VUS": 5}), encoding="utf-8")
        with self.select_menu(0, 0, 0, 1):
            rc = run_menu(workflows_dir)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("VUS=5", call)


class _SelectedMenu:
    def __init__(self, index: int | None) -> None:
        self.index = index

    def show(self) -> int | None:
        return self.index


class _ConfirmedTerminal(io.StringIO):
    def __init__(self) -> None:
        super().__init__("yes\n")

    def isatty(self) -> bool:
        return True


if __name__ == "__main__":
    unittest.main()

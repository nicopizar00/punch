from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.workflow import WorkflowError, load_workflow


VALID_WORKFLOW = """\
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
  environment:
    forward: [BASE_URL, RUN_ID]
    required: [RUN_ID]
  outputs:
    csv:
      path: reports/data/fixture.csv
"""


class LoadWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.workflow_path = self.root / "workflow.yaml"
        self.minimal_path = self.root / "minimal.yaml"
        (self.root / "docker-compose.yml").touch()
        self.write_workflow(self.workflow_path)
        self.minimal_path.write_text(
            VALID_WORKFLOW.replace(
                "  environment:\n    forward: [BASE_URL, RUN_ID]\n    required: [RUN_ID]\n"
                "  outputs:\n    csv:\n      path: reports/data/fixture.csv\n",
                "",
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_workflow(self, path: Path, replacement: tuple[str, str] | None = None) -> None:
        document = VALID_WORKFLOW
        if replacement is not None:
            original, updated = replacement
            self.assertIn(original, document)
            document = document.replace(original, updated, 1)
        path.write_text(document, encoding="utf-8")

    def assertWorkflowError(
        self, message: str, replacement: tuple[str, str] | None = None
    ) -> None:
        self.write_workflow(self.workflow_path, replacement)
        with self.assertRaisesRegex(WorkflowError, message):
            load_workflow(self.workflow_path)

    def test_loads_and_resolves_a_normalized_workflow(self) -> None:
        workflow = load_workflow(self.workflow_path)
        self.assertEqual(workflow.name, "csv-fixture")
        self.assertEqual(workflow.compose_service, "k6")
        self.assertEqual(workflow.k6_script, "/scripts/csv-fixture.js")
        self.assertEqual(workflow.forward_environment, ("BASE_URL", "RUN_ID"))
        self.assertEqual(workflow.required_environment, ("RUN_ID",))
        self.assertEqual(workflow.csv_output.path, self.root / "reports/data/fixture.csv")

    def test_rejects_unknown_properties(self) -> None:
        self.assertWorkflowError(
            "unknown field spec.k6.arguments",
            ("    script: /scripts/csv-fixture.js", "    script: /scripts/csv-fixture.js\n    arguments: []"),
        )

    def test_rejects_duplicate_yaml_keys(self) -> None:
        self.assertWorkflowError(
            "duplicate YAML key: name",
            ("  name: csv-fixture", "  name: csv-fixture\n  name: duplicate"),
        )

    def test_rejects_non_string_yaml_mapping_keys(self) -> None:
        self.assertWorkflowError(
            "YAML mapping keys must be strings",
            ("    script: /scripts/csv-fixture.js", "    ? [x]\n    : value"),
        )

    def test_rejects_aliases_and_anchors(self) -> None:
        self.assertWorkflowError(
            "YAML aliases and anchors are not supported",
            ("  name: csv-fixture", "  name: &workflow csv-fixture"),
        )
        self.assertWorkflowError(
            "YAML aliases and anchors are not supported",
            (
                "    forward: [BASE_URL, RUN_ID]",
                "    forward: &environment [BASE_URL, RUN_ID]\n    required: *environment",
            ),
        )

    def test_rejects_custom_yaml_tags(self) -> None:
        self.assertWorkflowError(
            "could not determine a constructor",
            ("  name: csv-fixture", "  name: !custom csv-fixture"),
        )

    def test_rejects_unsupported_version_and_kind(self) -> None:
        self.assertWorkflowError("apiVersion must be punch/v1", ("punch/v1", "punch/v2"))
        self.assertWorkflowError("kind must be K6Workflow", ("K6Workflow", "OtherWorkflow"))

    def test_requires_a_safe_workflow_name(self) -> None:
        self.assertWorkflowError("metadata.name must match", ("csv-fixture", "CSV Fixture"))

    def test_requires_environment_names_and_required_subset(self) -> None:
        self.assertWorkflowError(
            "environment.forward entries must match", ("[BASE_URL, RUN_ID]", "[not-valid]")
        )
        self.assertWorkflowError(
            "environment.required must also appear in environment.forward",
            ("[RUN_ID]", "[MISSING]"),
        )

    def test_rejects_compose_and_csv_paths_outside_working_directory(self) -> None:
        self.assertWorkflowError(
            "escapes spec.workingDirectory", ("docker-compose.yml", "../docker-compose.yml")
        )
        self.assertWorkflowError(
            "escapes spec.workingDirectory", ("reports/data/fixture.csv", "../fixture.csv")
        )

    def test_requires_workflow_file_beneath_working_directory(self) -> None:
        (self.root / "nested").mkdir()
        (self.root / "nested" / "docker-compose.yml").touch()
        self.assertWorkflowError(
            "workflow file must be beneath spec.workingDirectory", ("workingDirectory: .", "workingDirectory: nested")
        )

    def test_requires_an_existing_compose_file(self) -> None:
        self.assertWorkflowError("compose file does not exist", ("docker-compose.yml", "missing.yml"))

    def test_requires_an_absolute_container_script(self) -> None:
        self.assertWorkflowError(
            "spec.k6.script must be an absolute container path",
            ("/scripts/csv-fixture.js", "scripts/csv-fixture.js"),
        )

    def test_allows_workflow_without_environment_or_outputs(self) -> None:
        workflow = load_workflow(self.minimal_path)
        self.assertEqual(workflow.forward_environment, ())
        self.assertEqual(workflow.required_environment, ())
        self.assertIsNone(workflow.csv_output)


if __name__ == "__main__":
    unittest.main()

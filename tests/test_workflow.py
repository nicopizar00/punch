from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.workflow import Sizing, WorkflowError, load_workflow


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
  data:
    directory: data
    mountedAt: /scripts/data
    produces:
      - dataset: orders
        columns: [orderId]
        targets: [order-status]
    requires: [carts]
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
                "  data:\n    directory: data\n    mountedAt: /scripts/data\n"
                "    produces:\n      - dataset: orders\n        columns: [orderId]\n"
                "        targets: [order-status]\n    requires: [carts]\n",
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
        data = workflow.data
        self.assertEqual(data.directory, self.root / "data")
        self.assertEqual(data.mounted_at, "/scripts/data")
        self.assertEqual(data.produces[0].dataset, "orders")
        self.assertEqual(data.produces[0].columns, ("orderId",))
        self.assertEqual(data.produces[0].targets, ("order-status",))
        self.assertEqual(data.requires, ("carts",))
        self.assertEqual(data.host_path("orders"), self.root / "data" / "orders.csv")
        self.assertEqual(data.container_path("orders"), "/scripts/data/orders.csv")
        self.assertEqual(data.product("orders").columns, ("orderId",))
        self.assertIsNone(data.product("carts"))

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

    def test_rejects_compose_and_data_paths_outside_working_directory(self) -> None:
        self.assertWorkflowError(
            "escapes spec.workingDirectory", ("docker-compose.yml", "../docker-compose.yml")
        )
        self.assertWorkflowError("escapes", ("directory: data", "directory: ../outside"))

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

    def test_allows_workflow_without_environment_or_data(self) -> None:
        workflow = load_workflow(self.minimal_path)
        self.assertEqual(workflow.forward_environment, ())
        self.assertEqual(workflow.required_environment, ())
        self.assertIsNone(workflow.data)
        self.assertEqual(workflow.description, "")

    def test_reads_optional_metadata_description(self) -> None:
        self.write_workflow(
            self.workflow_path,
            ("  name: csv-fixture\n", "  name: csv-fixture\n  description: Writes orders.\n"),
        )
        self.assertEqual(load_workflow(self.workflow_path).description, "Writes orders.")

    def test_rejects_empty_metadata_description(self) -> None:
        self.assertWorkflowError(
            "metadata.description must be a non-empty string",
            ("  name: csv-fixture\n", "  name: csv-fixture\n  description: ''\n"),
        )

    def test_data_env_name(self) -> None:
        from punch.workflow import data_env_name
        self.assertEqual(data_env_name("orders"), "DATA_ORDERS_CSV")
        self.assertEqual(data_env_name("cart-items"), "DATA_CART_ITEMS_CSV")

    def test_rejects_legacy_csv_output(self) -> None:
        self.assertWorkflowError(
            "unknown field outputs.csv",
            ("  data:\n", "  outputs:\n    csv:\n      path: reports/x.csv\n  data:\n"),
        )

    def test_rejects_legacy_inputs(self) -> None:
        self.assertWorkflowError(
            "unknown field spec.inputs",
            ("  data:\n", "  inputs:\n    csv:\n      path: data/x.csv\n  data:\n"),
        )

    def test_rejects_relative_mounted_at(self) -> None:
        self.assertWorkflowError(
            "spec.data.mountedAt must be an absolute container path",
            ("mountedAt: /scripts/data", "mountedAt: scripts/data"),
        )

    def test_rejects_data_without_produces_requires_or_optional(self) -> None:
        self.assertWorkflowError(
            "spec.data must declare produces, requires, or optional",
            (
                "    produces:\n      - dataset: orders\n        columns: [orderId]\n"
                "        targets: [order-status]\n    requires: [carts]\n",
                "",
            ),
        )

    def test_rejects_invalid_dataset_name(self) -> None:
        self.assertWorkflowError("dataset must match", ("dataset: orders", "dataset: Orders"))

    def test_rejects_invalid_required_dataset_name(self) -> None:
        self.assertWorkflowError("requires dataset must match", ("requires: [carts]", "requires: [Carts]"))

    def test_rejects_duplicate_produced_dataset(self) -> None:
        self.assertWorkflowError(
            "duplicate dataset in spec.data.produces: orders",
            (
                "        targets: [order-status]\n",
                "        targets: [order-status]\n      - dataset: orders\n"
                "        columns: [orderId]\n        targets: [order-status]\n",
            ),
        )

    def test_rejects_duplicate_required_dataset(self) -> None:
        self.assertWorkflowError(
            "duplicate dataset in spec.data.requires: carts",
            ("requires: [carts]", "requires: [carts, carts]"),
        )

    def test_parses_optional_datasets(self) -> None:
        self.write_workflow(
            self.workflow_path,
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [orders-in, extra]\n"),
        )
        workflow = load_workflow(self.workflow_path)
        self.assertEqual(workflow.data.optional, ("orders-in", "extra"))
        self.assertEqual(workflow.data.requires, ("carts",))

    def test_optional_alone_is_enough(self) -> None:
        self.write_workflow(
            self.workflow_path,
            (
                "    produces:\n      - dataset: orders\n        columns: [orderId]\n"
                "        targets: [order-status]\n    requires: [carts]\n",
                "    optional: [carts]\n",
            ),
        )
        workflow = load_workflow(self.workflow_path)
        self.assertEqual(workflow.data.optional, ("carts",))
        self.assertEqual(workflow.data.requires, ())
        self.assertEqual(workflow.data.produces, ())

    def test_optional_defaults_to_empty(self) -> None:
        self.assertEqual(load_workflow(self.workflow_path).data.optional, ())

    def test_rejects_empty_optional(self) -> None:
        self.assertWorkflowError(
            "spec.data.optional must be a non-empty list",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: []\n"),
        )

    def test_rejects_invalid_optional_dataset_name(self) -> None:
        self.assertWorkflowError(
            "optional dataset must match",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [Bad]\n"),
        )

    def test_rejects_duplicate_optional_dataset(self) -> None:
        self.assertWorkflowError(
            "duplicate dataset in spec.data.optional: extra",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [extra, extra]\n"),
        )

    def test_rejects_dataset_both_required_and_optional(self) -> None:
        self.assertWorkflowError(
            "dataset carts is both required and optional",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [carts]\n"),
        )

    def test_rejects_empty_columns(self) -> None:
        self.assertWorkflowError("columns must be a non-empty list", ("columns: [orderId]", "columns: []"))

    def test_rejects_invalid_column_name(self) -> None:
        self.assertWorkflowError("columns entries must match", ("columns: [orderId]", "columns: [order-id]"))

    def test_rejects_duplicate_column(self) -> None:
        self.assertWorkflowError("duplicate column", ("columns: [orderId]", "columns: [orderId, orderId]"))

    def test_rejects_empty_targets(self) -> None:
        self.assertWorkflowError("targets must be a non-empty list", ("targets: [order-status]", "targets: []"))

    def test_rejects_invalid_target_name(self) -> None:
        self.assertWorkflowError("targets entries must match", ("targets: [order-status]", "targets: [Order]"))

    def test_rejects_unknown_product_field(self) -> None:
        self.assertWorkflowError(
            "unknown field spec.data.produces\\[0\\].path",
            ("        targets: [order-status]\n", "        targets: [order-status]\n        path: x.csv\n"),
        )

    def test_product_recommended_defaults_to_false(self) -> None:
        workflow = load_workflow(self.workflow_path)
        self.assertFalse(workflow.data.produces[0].recommended)

    def test_product_recommended_true_is_loaded(self) -> None:
        self.write_workflow(
            self.workflow_path,
            ("        targets: [order-status]\n",
             "        targets: [order-status]\n        recommended: true\n"),
        )
        self.assertTrue(load_workflow(self.workflow_path).data.produces[0].recommended)

    def test_rejects_non_boolean_recommended(self) -> None:
        self.assertWorkflowError(
            "spec.data.produces\\[0\\].recommended must be a boolean",
            ("        targets: [order-status]\n",
             "        targets: [order-status]\n        recommended: \"yes\"\n"),
        )

    SIZING_ANCHOR = "    requires: [carts]\n"

    def with_sizing(self, block: str) -> tuple[str, str]:
        return (self.SIZING_ANCHOR, self.SIZING_ANCHOR + block)

    def test_sizing_defaults_to_none(self) -> None:
        self.assertIsNone(load_workflow(self.workflow_path).sizing)

    def test_reads_sizing(self) -> None:
        self.write_workflow(
            self.workflow_path,
            self.with_sizing(
                "  sizing:\n    iterationSeconds: 1.5\n    maxSeconds: 270\n    margin: 0.15\n"
            ),
        )
        self.assertEqual(
            load_workflow(self.workflow_path).sizing,
            Sizing(iteration_seconds=1.5, max_seconds=270, margin=0.15),
        )

    def test_empty_sizing_is_a_target_with_zero_margin(self) -> None:
        self.write_workflow(self.workflow_path, self.with_sizing("  sizing: {}\n"))
        self.assertEqual(load_workflow(self.workflow_path).sizing, Sizing())

    def test_rejects_invalid_sizing(self) -> None:
        cases = [
            ("  sizing:\n", "spec.sizing must be a mapping"),
            ("  sizing:\n    unknown: 1\n", "unknown field spec.sizing.unknown"),
            ("  sizing:\n    margin: -1\n", "spec.sizing.margin must be 0 or greater"),
            ("  sizing:\n    iterationSeconds: 0\n",
             "spec.sizing.iterationSeconds must be greater than 0"),
            ("  sizing:\n    maxSeconds: -5\n", "spec.sizing.maxSeconds must be greater than 0"),
            ("  sizing:\n    maxSeconds: true\n", "spec.sizing.maxSeconds must be a number"),
            ('  sizing:\n    iterationSeconds: "1"\n',
             "spec.sizing.iterationSeconds must be a number"),
            ("  sizing:\n    margin: .inf\n", "spec.sizing.margin must be a number"),
            ("  sizing:\n    iterationSeconds: .nan\n",
             "spec.sizing.iterationSeconds must be a number"),
        ]
        for block, message in cases:
            with self.subTest(block=block):
                self.assertWorkflowError(message, self.with_sizing(block))

if __name__ == "__main__":
    unittest.main()

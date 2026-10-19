from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.job import JobError, Sizing, load_job

VALID_JOB = """\
apiVersion: punch/v1
kind: K6Job
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


class LoadJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.job_path = self.root / "job.yaml"
        self.minimal_path = self.root / "minimal.yaml"
        (self.root / "docker-compose.yml").touch()
        self.write_job(self.job_path)
        self.minimal_path.write_text(
            VALID_JOB.replace(
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

    def write_job(self, path: Path, replacement: tuple[str, str] | None = None) -> None:
        document = VALID_JOB
        if replacement is not None:
            original, updated = replacement
            self.assertIn(original, document)
            document = document.replace(original, updated, 1)
        path.write_text(document, encoding="utf-8")

    def assertJobError(self, message: str, replacement: tuple[str, str] | None = None) -> None:
        self.write_job(self.job_path, replacement)
        with self.assertRaisesRegex(JobError, message):
            load_job(self.job_path)

    def test_loads_and_resolves_a_normalized_job(self) -> None:
        job = load_job(self.job_path)
        self.assertEqual(job.name, "csv-fixture")
        self.assertEqual(job.compose_service, "k6")
        self.assertEqual(job.k6_script, "/scripts/csv-fixture.js")
        self.assertEqual(job.forward_environment, ("BASE_URL", "RUN_ID"))
        self.assertEqual(job.required_environment, ("RUN_ID",))
        data = job.data
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

    def test_the_old_kind_names_its_replacement(self) -> None:
        self.assertJobError(
            "kind K6Workflow was renamed to K6Job; change the kind line",
            ("kind: K6Job", "kind: K6Workflow"),
        )

    def test_rejects_unknown_properties(self) -> None:
        self.assertJobError(
            "unknown field spec.k6.arguments",
            (
                "    script: /scripts/csv-fixture.js",
                "    script: /scripts/csv-fixture.js\n    arguments: []",
            ),
        )

    def test_rejects_duplicate_yaml_keys(self) -> None:
        self.assertJobError(
            "duplicate YAML key: name",
            ("  name: csv-fixture", "  name: csv-fixture\n  name: duplicate"),
        )

    def test_rejects_non_string_yaml_mapping_keys(self) -> None:
        self.assertJobError(
            "YAML mapping keys must be strings",
            ("    script: /scripts/csv-fixture.js", "    ? [x]\n    : value"),
        )

    def test_rejects_aliases_and_anchors(self) -> None:
        self.assertJobError(
            "YAML aliases and anchors are not supported",
            ("  name: csv-fixture", "  name: &job csv-fixture"),
        )
        self.assertJobError(
            "YAML aliases and anchors are not supported",
            (
                "    forward: [BASE_URL, RUN_ID]",
                "    forward: &environment [BASE_URL, RUN_ID]\n    required: *environment",
            ),
        )

    def test_rejects_custom_yaml_tags(self) -> None:
        self.assertJobError(
            "could not determine a constructor",
            ("  name: csv-fixture", "  name: !custom csv-fixture"),
        )

    def test_rejects_unsupported_version_and_kind(self) -> None:
        self.assertJobError("apiVersion must be punch/v1", ("punch/v1", "punch/v2"))
        self.assertJobError("kind must be K6Job", ("K6Job", "OtherJob"))

    def test_requires_a_safe_job_name(self) -> None:
        self.assertJobError("metadata.name must match", ("csv-fixture", "CSV Fixture"))

    def test_requires_environment_names_and_required_subset(self) -> None:
        self.assertJobError(
            "environment.forward entries must match", ("[BASE_URL, RUN_ID]", "[not-valid]")
        )
        self.assertJobError(
            "environment.required must also appear in environment.forward",
            ("[RUN_ID]", "[MISSING]"),
        )

    def test_rejects_compose_and_data_paths_outside_working_directory(self) -> None:
        self.assertJobError(
            "escapes spec.workingDirectory", ("docker-compose.yml", "../docker-compose.yml")
        )
        self.assertJobError("escapes", ("directory: data", "directory: ../outside"))

    def test_requires_job_file_beneath_working_directory(self) -> None:
        (self.root / "nested").mkdir()
        (self.root / "nested" / "docker-compose.yml").touch()
        self.assertJobError(
            "job file must be beneath spec.workingDirectory",
            ("workingDirectory: .", "workingDirectory: nested"),
        )

    def test_requires_an_existing_compose_file(self) -> None:
        self.assertJobError("compose file does not exist", ("docker-compose.yml", "missing.yml"))

    def test_requires_an_absolute_container_script(self) -> None:
        self.assertJobError(
            "spec.k6.script must be an absolute container path",
            ("/scripts/csv-fixture.js", "scripts/csv-fixture.js"),
        )

    def test_k6_config_defaults_to_none(self) -> None:
        self.assertIsNone(load_job(self.job_path).k6_config)

    def test_reads_k6_config_beneath_working_directory(self) -> None:
        (self.root / "options").mkdir()
        (self.root / "options" / "5-iterations.json").write_text(
            '{"iterations": 5}', encoding="utf-8"
        )
        self.write_job(
            self.job_path,
            (
                "    script: /scripts/csv-fixture.js",
                "    script: /scripts/csv-fixture.js\n    config: options/5-iterations.json",
            ),
        )
        self.assertEqual(
            load_job(self.job_path).k6_config, self.root / "options" / "5-iterations.json"
        )

    def test_rejects_invalid_k6_config(self) -> None:
        (self.root / "list.json").write_text("[1]", encoding="utf-8")
        (self.root / "broken.json").write_text("{", encoding="utf-8")
        cases = {
            "spec.k6.config escapes spec.workingDirectory": "../outside.json",
            "spec.k6.config does not exist": "missing.json",
            "k6 config .*list.json must be a JSON object": "list.json",
            "could not read k6 config .*broken.json": "broken.json",
            "spec.k6.config must be a non-empty string": '""',
        }
        for message, value in cases.items():
            with self.subTest(value=value):
                self.assertJobError(
                    message,
                    (
                        "    script: /scripts/csv-fixture.js",
                        f"    script: /scripts/csv-fixture.js\n    config: {value}",
                    ),
                )

    def test_allows_job_without_environment_or_data(self) -> None:
        job = load_job(self.minimal_path)
        self.assertEqual(job.forward_environment, ())
        self.assertEqual(job.required_environment, ())
        self.assertIsNone(job.data)
        self.assertEqual(job.description, "")

    def test_reads_optional_metadata_description(self) -> None:
        self.write_job(
            self.job_path,
            ("  name: csv-fixture\n", "  name: csv-fixture\n  description: Writes orders.\n"),
        )
        self.assertEqual(load_job(self.job_path).description, "Writes orders.")

    def test_rejects_empty_metadata_description(self) -> None:
        self.assertJobError(
            "metadata.description must be a non-empty string",
            ("  name: csv-fixture\n", "  name: csv-fixture\n  description: ''\n"),
        )

    def test_data_env_name(self) -> None:
        from punch.job import data_env_name

        self.assertEqual(data_env_name("orders"), "DATA_ORDERS_CSV")
        self.assertEqual(data_env_name("cart-items"), "DATA_CART_ITEMS_CSV")

    def test_rejects_legacy_csv_output(self) -> None:
        self.assertJobError(
            "unknown field outputs.csv",
            ("  data:\n", "  outputs:\n    csv:\n      path: reports/x.csv\n  data:\n"),
        )

    def test_rejects_legacy_inputs(self) -> None:
        self.assertJobError(
            "unknown field spec.inputs",
            ("  data:\n", "  inputs:\n    csv:\n      path: data/x.csv\n  data:\n"),
        )

    def test_rejects_relative_mounted_at(self) -> None:
        self.assertJobError(
            "spec.data.mountedAt must be an absolute container path",
            ("mountedAt: /scripts/data", "mountedAt: scripts/data"),
        )

    def test_rejects_data_without_produces_requires_or_optional(self) -> None:
        self.assertJobError(
            "spec.data must declare produces, requires, or optional",
            (
                "    produces:\n      - dataset: orders\n        columns: [orderId]\n"
                "        targets: [order-status]\n    requires: [carts]\n",
                "",
            ),
        )

    def test_rejects_invalid_dataset_name(self) -> None:
        self.assertJobError("dataset must match", ("dataset: orders", "dataset: Orders"))

    def test_rejects_invalid_required_dataset_name(self) -> None:
        self.assertJobError(
            "requires dataset must match", ("requires: [carts]", "requires: [Carts]")
        )

    def test_rejects_duplicate_produced_dataset(self) -> None:
        self.assertJobError(
            "duplicate dataset in spec.data.produces: orders",
            (
                "        targets: [order-status]\n",
                "        targets: [order-status]\n      - dataset: orders\n"
                "        columns: [orderId]\n        targets: [order-status]\n",
            ),
        )

    def test_rejects_duplicate_required_dataset(self) -> None:
        self.assertJobError(
            "duplicate dataset in spec.data.requires: carts",
            ("requires: [carts]", "requires: [carts, carts]"),
        )

    def test_parses_optional_datasets(self) -> None:
        self.write_job(
            self.job_path,
            (
                "    requires: [carts]\n",
                "    requires: [carts]\n    optional: [orders-in, extra]\n",
            ),
        )
        job = load_job(self.job_path)
        self.assertEqual(job.data.optional, ("orders-in", "extra"))
        self.assertEqual(job.data.requires, ("carts",))

    def test_optional_alone_is_enough(self) -> None:
        self.write_job(
            self.job_path,
            (
                "    produces:\n      - dataset: orders\n        columns: [orderId]\n"
                "        targets: [order-status]\n    requires: [carts]\n",
                "    optional: [carts]\n",
            ),
        )
        job = load_job(self.job_path)
        self.assertEqual(job.data.optional, ("carts",))
        self.assertEqual(job.data.requires, ())
        self.assertEqual(job.data.produces, ())

    def test_optional_defaults_to_empty(self) -> None:
        self.assertEqual(load_job(self.job_path).data.optional, ())

    def test_rejects_empty_optional(self) -> None:
        self.assertJobError(
            "spec.data.optional must be a non-empty list",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: []\n"),
        )

    def test_rejects_invalid_optional_dataset_name(self) -> None:
        self.assertJobError(
            "optional dataset must match",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [Bad]\n"),
        )

    def test_rejects_duplicate_optional_dataset(self) -> None:
        self.assertJobError(
            "duplicate dataset in spec.data.optional: extra",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [extra, extra]\n"),
        )

    def test_rejects_dataset_both_required_and_optional(self) -> None:
        self.assertJobError(
            "dataset carts is both required and optional",
            ("    requires: [carts]\n", "    requires: [carts]\n    optional: [carts]\n"),
        )

    def test_rejects_empty_columns(self) -> None:
        self.assertJobError(
            "columns must be a non-empty list", ("columns: [orderId]", "columns: []")
        )

    def test_rejects_invalid_column_name(self) -> None:
        self.assertJobError(
            "columns entries must match", ("columns: [orderId]", "columns: [order-id]")
        )

    def test_rejects_duplicate_column(self) -> None:
        self.assertJobError(
            "duplicate column", ("columns: [orderId]", "columns: [orderId, orderId]")
        )

    def test_rejects_empty_targets(self) -> None:
        self.assertJobError(
            "targets must be a non-empty list", ("targets: [order-status]", "targets: []")
        )

    def test_rejects_invalid_target_name(self) -> None:
        self.assertJobError(
            "targets entries must match", ("targets: [order-status]", "targets: [Order]")
        )

    def test_rejects_unknown_product_field(self) -> None:
        self.assertJobError(
            "unknown field spec.data.produces\\[0\\].path",
            (
                "        targets: [order-status]\n",
                "        targets: [order-status]\n        path: x.csv\n",
            ),
        )

    def test_product_recommended_defaults_to_false(self) -> None:
        job = load_job(self.job_path)
        self.assertFalse(job.data.produces[0].recommended)

    def test_product_recommended_true_is_loaded(self) -> None:
        self.write_job(
            self.job_path,
            (
                "        targets: [order-status]\n",
                "        targets: [order-status]\n        recommended: true\n",
            ),
        )
        self.assertTrue(load_job(self.job_path).data.produces[0].recommended)

    def test_rejects_non_boolean_recommended(self) -> None:
        self.assertJobError(
            "spec.data.produces\\[0\\].recommended must be a boolean",
            (
                "        targets: [order-status]\n",
                '        targets: [order-status]\n        recommended: "yes"\n',
            ),
        )

    SIZING_ANCHOR = "    requires: [carts]\n"

    def with_sizing(self, block: str) -> tuple[str, str]:
        return (self.SIZING_ANCHOR, self.SIZING_ANCHOR + block)

    def test_sizing_defaults_to_none(self) -> None:
        self.assertIsNone(load_job(self.job_path).sizing)

    def test_reads_sizing(self) -> None:
        self.write_job(
            self.job_path,
            self.with_sizing(
                "  sizing:\n    iterationSeconds: 1.5\n    maxSeconds: 270\n    margin: 0.15\n"
            ),
        )
        self.assertEqual(
            load_job(self.job_path).sizing,
            Sizing(iteration_seconds=1.5, max_seconds=270, margin=0.15),
        )

    def test_empty_sizing_is_a_target_with_zero_margin(self) -> None:
        self.write_job(self.job_path, self.with_sizing("  sizing: {}\n"))
        self.assertEqual(load_job(self.job_path).sizing, Sizing())

    def test_rejects_invalid_sizing(self) -> None:
        cases = [
            ("  sizing:\n", "spec.sizing must be a mapping"),
            ("  sizing:\n    unknown: 1\n", "unknown field spec.sizing.unknown"),
            ("  sizing:\n    margin: -1\n", "spec.sizing.margin must be 0 or greater"),
            (
                "  sizing:\n    iterationSeconds: 0\n",
                "spec.sizing.iterationSeconds must be greater than 0",
            ),
            ("  sizing:\n    maxSeconds: -5\n", "spec.sizing.maxSeconds must be greater than 0"),
            ("  sizing:\n    maxSeconds: true\n", "spec.sizing.maxSeconds must be a number"),
            (
                '  sizing:\n    iterationSeconds: "1"\n',
                "spec.sizing.iterationSeconds must be a number",
            ),
            ("  sizing:\n    margin: .inf\n", "spec.sizing.margin must be a number"),
            (
                "  sizing:\n    iterationSeconds: .nan\n",
                "spec.sizing.iterationSeconds must be a number",
            ),
        ]
        for block, message in cases:
            with self.subTest(block=block):
                self.assertJobError(message, self.with_sizing(block))


if __name__ == "__main__":
    unittest.main()

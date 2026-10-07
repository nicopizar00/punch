from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import CatalogError, load_catalog

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        shutil.copy(FIXTURES / "docker-compose.yml", self.root / "docker-compose.yml")
        shutil.copy(FIXTURES / "data-output.yaml", self.root / "data-output.yaml")
        shutil.copy(FIXTURES / "data-input.yaml", self.root / "data-input.yaml")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def copy_producer(self, name: str, *, recommended: bool = False) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        text = text.replace("name: data-producer", f"name: {name}")
        if recommended:
            text = text.replace("targets: [data-consumer]",
                                "targets: [data-consumer]\n        recommended: true")
        (self.root / f"{name}.yaml").write_text(text, encoding="utf-8")

    def test_links_producers_and_consumers(self) -> None:
        catalog = load_catalog(self.root)
        self.assertEqual(catalog.producers_of("carts"), ("data-producer",))
        self.assertEqual(catalog.consumers_of("carts"), ("data-consumer",))
        self.assertEqual(catalog.producers_of("unknown"), ())

    def test_unknown_target_fails(self) -> None:
        self.edit("data-output.yaml", "targets: [data-consumer]", "targets: [ghost]")
        with self.assertRaisesRegex(CatalogError, 'data-producer targets unknown workflow "ghost"'):
            load_catalog(self.root)

    def test_target_that_does_not_require_dataset_fails(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "requires: [other]")
        with self.assertRaisesRegex(CatalogError, 'data-consumer does not require "carts"'):
            load_catalog(self.root)

    def test_required_dataset_without_producer_fails(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "requires: [carts, orders]")
        with self.assertRaisesRegex(
            CatalogError, 'data-consumer requires "orders" but no workflow produces it'
        ):
            load_catalog(self.root)

    def test_target_may_name_an_optional_consumer(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "optional: [carts]")
        catalog = load_catalog(self.root)
        self.assertEqual(catalog.consumers_of("carts"), ("data-consumer",))

    def test_optional_dataset_without_producer_is_allowed(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "requires: [carts]\n    optional: [extras]")
        catalog = load_catalog(self.root)
        self.assertEqual(catalog.producers_of("extras"), ())

    def test_conflicting_columns_across_producers_fail(self) -> None:
        second = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        second = second.replace("name: data-producer", "name: data-producer-2")
        second = second.replace("columns: [cartId, productId, sid]", "columns: [cartId]")
        (self.root / "data-output-2.yaml").write_text(second, encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, 'producers of "carts" declare different columns'):
            load_catalog(self.root)

    def test_duplicate_workflow_names_fail(self) -> None:
        shutil.copy(self.root / "data-input.yaml", self.root / "copy.yaml")
        with self.assertRaisesRegex(CatalogError, 'duplicate workflow name "data-consumer"'):
            load_catalog(self.root)

    def test_invalid_workflow_file_is_reported_with_its_name(self) -> None:
        (self.root / "broken.yaml").write_text("apiVersion: nope\n", encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, "broken.yaml"):
            load_catalog(self.root)

    def test_recommended_producer_is_the_flagged_one(self) -> None:
        self.copy_producer("other-producer", recommended=True)
        catalog = load_catalog(self.root)
        self.assertEqual(catalog.producers_of("carts"), ("data-producer", "other-producer"))
        self.assertEqual(catalog.recommended_producer("carts"), "other-producer")

    def test_no_recommended_producer_returns_none(self) -> None:
        catalog = load_catalog(self.root)
        self.assertIsNone(catalog.recommended_producer("carts"))
        self.assertIsNone(catalog.recommended_producer("unknown"))

    def test_first_recommended_producer_by_name_wins(self) -> None:
        self.copy_producer("zz-producer", recommended=True)
        self.copy_producer("mm-producer", recommended=True)
        self.assertEqual(load_catalog(self.root).recommended_producer("carts"), "mm-producer")


if __name__ == "__main__":
    unittest.main()

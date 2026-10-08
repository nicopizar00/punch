from __future__ import annotations

import sys
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.sizing import (
    SizingError,
    is_sizable_producer,
    parse_duration,
    producer_environment,
    rows_needed,
    size_producer,
    sizing_pairs,
)

SHAPE = ["ITERATIONS", "VUS", "DURATION"]
PRODUCER_SIZING = {"iterationSeconds": 1, "maxSeconds": 270}
TARGET_SIZING = {"iterationSeconds": 2, "margin": 0.15}


def workflow_yaml(
    name: str,
    *,
    forward: list[str],
    produces: dict[str, list[str]] | None = None,
    requires: list[str] | None = None,
    sizing: dict | None = None,
) -> str:
    lines = [
        "apiVersion: punch/v1", "kind: K6Workflow", "metadata:", f"  name: {name}",
        "spec:", "  workingDirectory: .", "  compose:", "    file: docker-compose.yml",
        "    service: k6", "  k6:", f"    script: /scripts/{name}.js",
        "  environment:", f"    forward: [{', '.join(forward)}]",
        "  data:", "    directory: data", "    mountedAt: /scripts/data",
    ]
    if produces:
        lines.append("    produces:")
        for dataset, targets in produces.items():
            lines += [
                f"      - dataset: {dataset}",
                "        columns: [id]",
                f"        targets: [{', '.join(targets)}]",
            ]
    if requires:
        lines.append(f"    requires: [{', '.join(requires)}]")
    if sizing is not None:
        lines.append("  sizing:" if sizing else "  sizing: {}")
        lines += [f"    {key}: {value}" for key, value in sizing.items()]
    return "\n".join(lines) + "\n"


class SizingCase(unittest.TestCase):
    """producer → carts → consumer; both sized and forwarding the full shape."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "docker-compose.yml").touch()
        self.write_producer()
        self.write_consumer()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, **kwargs) -> None:
        (self.root / f"{name}.yaml").write_text(workflow_yaml(name, **kwargs), encoding="utf-8")

    def write_producer(self, *, forward=SHAPE, sizing=PRODUCER_SIZING, targets=("consumer",)) -> None:
        self.write("producer", forward=forward, produces={"carts": list(targets)}, sizing=sizing)

    def write_consumer(self, *, forward=SHAPE, sizing=TARGET_SIZING, name="consumer") -> None:
        self.write(name, forward=forward, requires=["carts"], sizing=sizing)

    def size(self, source, target="consumer", **kwargs):
        catalog = load_catalog(self.root)
        return size_producer(catalog.workflows["producer"], target, catalog, source, **kwargs)

    def assertNotEstimable(self, message: str, source, target="consumer") -> None:
        with self.assertRaisesRegex(SizingError, message):
            self.size(source, target)


class ParseDurationTests(unittest.TestCase):
    def test_parses_k6_durations(self) -> None:
        cases = {"5m": 300, "1h30m": 5400, "90s": 90, "1m30s": 90, "1.5m": 90,
                 "500ms": Fraction(1, 2)}
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), seconds)

    def test_rejects_other_strings(self) -> None:
        for text in ("", "5", "5x", "m", "0s", "5m ", "-5m", "5 m"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_duration(text)


class SizingMathTests(SizingCase):
    def test_iterations_shape_is_rows_needed(self) -> None:
        plan = self.size({"ITERATIONS": "50"})
        self.assertEqual(plan.datasets, ("carts",))
        self.assertEqual(dict(plan.shape), {"ITERATIONS": "50"})
        self.assertEqual((plan.rows_needed, plan.iterations, plan.vus), (50, 58, 1))
        self.assertEqual(plan.margin, 0.15)
        self.assertEqual(plan.estimated_seconds, 58.0)

    def test_iterations_wins_over_duration(self) -> None:
        plan = self.size({"ITERATIONS": "5", "VUS": "5", "DURATION": "5m"})
        self.assertEqual(plan.rows_needed, 5)
        self.assertEqual(dict(plan.shape), {"ITERATIONS": "5", "VUS": "5", "DURATION": "5m"})

    def test_duration_shape_uses_target_pace(self) -> None:
        plan = self.size({"VUS": "5", "DURATION": "5m"})
        # ceil(5 × 300 / 2) = 750; ceil(750 × 1.15) = 863; ceil(863 × 1 / 270) = 4
        self.assertEqual((plan.rows_needed, plan.iterations, plan.vus), (750, 863, 4))
        self.assertEqual(plan.estimated_seconds, 215.75)

    def test_ceilings_use_exact_decimals(self) -> None:
        self.write_consumer(sizing={"margin": 0.1})
        # 20 × 1.1 is 22.000000000000004 in binary floats
        self.assertEqual(self.size({"ITERATIONS": "20"}).iterations, 22)

    def test_vus_never_exceed_iterations(self) -> None:
        self.write_producer(sizing={"iterationSeconds": 600, "maxSeconds": 270})
        self.write_consumer(sizing={"margin": 0})
        plan = self.size({"ITERATIONS": "2"})
        self.assertEqual((plan.iterations, plan.vus), (2, 2))
        self.assertEqual(plan.estimated_seconds, 600.0)

    def test_shape_only_counts_forwarded_names(self) -> None:
        self.write_consumer(forward=["ITERATIONS", "VUS"])
        self.assertNotEstimable("consumer does not forward DURATION", {"VUS": "5", "DURATION": "5m"})

    def test_target_forwarding_neither(self) -> None:
        self.write_consumer(forward=["VUS"])
        self.assertNotEstimable("consumer forwards neither ITERATIONS nor DURATION", {"ITERATIONS": "5"})

    def test_shape_sets_neither(self) -> None:
        self.assertNotEstimable("shape sets neither ITERATIONS nor DURATION", {"VUS": "5"})
        self.assertNotEstimable("shape sets neither ITERATIONS nor DURATION", {})

    def test_duration_needs_vus(self) -> None:
        self.assertNotEstimable("DURATION needs VUS", {"DURATION": "5m"})

    def test_duration_needs_target_pace(self) -> None:
        self.write_consumer(sizing={"margin": 0.15})
        self.assertNotEstimable(
            "consumer declares no sizing.iterationSeconds", {"VUS": "5", "DURATION": "5m"}
        )
        self.assertEqual(self.size({"ITERATIONS": "5"}).rows_needed, 5)

    def test_invalid_values_are_reasons_not_crashes(self) -> None:
        self.assertNotEstimable('invalid ITERATIONS "0"', {"ITERATIONS": "0"})
        self.assertNotEstimable('invalid ITERATIONS "lots"', {"ITERATIONS": "lots"})
        self.assertNotEstimable('invalid VUS "x"', {"VUS": "x", "DURATION": "5m"})
        self.assertNotEstimable('invalid DURATION "5"', {"VUS": "5", "DURATION": "5"})

    def test_empty_values_count_as_unset(self) -> None:
        plan = self.size({"ITERATIONS": "", "VUS": "5", "DURATION": "5m"})
        self.assertEqual(plan.rows_needed, 750)
        self.assertNotIn("ITERATIONS", plan.shape)

    def test_rows_needed_reads_the_raw_source(self) -> None:
        target = load_catalog(self.root).workflows["consumer"]
        self.assertEqual(rows_needed(target, {"ITERATIONS": "7", "UNRELATED": "x"}), 7)

    def test_preset_is_carried(self) -> None:
        self.assertEqual(self.size({"ITERATIONS": "5"}, preset="5-iterations").preset, "5-iterations")


class SizingEligibilityTests(SizingCase):
    def test_producer_needs_pace_budget_and_shape_forwarding(self) -> None:
        catalog = load_catalog(self.root)
        self.assertTrue(is_sizable_producer(catalog.workflows["producer"]))
        for kwargs in (
            {"sizing": {"iterationSeconds": 1}},
            {"sizing": {"maxSeconds": 270}},
            {"sizing": None},
            {"forward": ["VUS", "DURATION"]},
            {"forward": ["ITERATIONS", "DURATION"]},
        ):
            with self.subTest(**{key: str(value) for key, value in kwargs.items()}):
                self.write_producer(**kwargs)
                self.assertNotEstimable(
                    "producer is not a sizable producer: needs sizing.iterationSeconds, "
                    "sizing.maxSeconds, and forwarded ITERATIONS and VUS",
                    {"ITERATIONS": "5"},
                )

    def test_unknown_and_unsized_targets(self) -> None:
        self.assertNotEstimable("producer does not produce data for nope", {"ITERATIONS": "5"}, "nope")
        self.write_consumer(sizing=None)
        self.assertNotEstimable("consumer declares no spec.sizing", {"ITERATIONS": "5"})

    def test_sizing_pairs_skip_unsized_targets(self) -> None:
        self.write_producer(targets=("consumer", "other"))
        self.write_consumer(name="other", sizing=None)
        catalog = load_catalog(self.root)
        self.assertEqual(sizing_pairs(catalog.workflows["producer"], catalog), (("carts", "consumer"),))
        self.assertEqual(sizing_pairs(catalog.workflows["consumer"], catalog), ())

    def test_non_sizable_producer_has_no_pairs(self) -> None:
        self.write_producer(sizing=None)
        catalog = load_catalog(self.root)
        self.assertEqual(sizing_pairs(catalog.workflows["producer"], catalog), ())


class ProducerEnvironmentTests(SizingCase):
    def test_overrides_shape_and_drops_duration(self) -> None:
        plan = self.size({"ITERATIONS": "50"})
        environment = {"DURATION": "5m", "VUS": "9", "BASE_URL": "http://x"}
        self.assertEqual(
            producer_environment(environment, plan),
            {"BASE_URL": "http://x", "ITERATIONS": "58", "VUS": "1"},
        )
        self.assertEqual(environment["DURATION"], "5m")  # input untouched


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.sizing import (
    SizingError,
    input_warnings,
    is_sizable_producer,
    next_hint,
    parse_duration,
    rows_needed,
    shortfall,
    size_producer,
    sizing_evidence,
    sizing_pairs,
    summary_lines,
    target_shape,
    write_producer_config,
)

PRODUCER_SIZING = {"iterationSeconds": 1, "maxSeconds": 270}
TARGET_SIZING = {"iterationSeconds": 2, "margin": 0.15}


def iterations(count: int, vus: int = 1) -> dict:
    return {"scenarios": {"default": {
        "executor": "shared-iterations", "vus": vus, "iterations": count, "maxDuration": "5m",
    }}}


def constant(vus: int, duration: str) -> dict:
    return {"scenarios": {"default": {"executor": "constant-vus", "vus": vus, "duration": duration}}}


def workflow_yaml(
    name: str,
    *,
    produces: dict[str, list[str]] | None = None,
    requires: list[str] | None = None,
    sizing: dict | None = None,
    config: str | None = None,
) -> str:
    lines = [
        "apiVersion: punch/v1", "kind: K6Workflow", "metadata:", f"  name: {name}",
        "spec:", "  workingDirectory: .", "  compose:", "    file: docker-compose.yml",
        "    service: k6", "  k6:", f"    script: /scripts/{name}.js",
    ]
    if config is not None:
        lines.append(f"    config: {config}")
    lines += ["  data:", "    directory: data", "    mountedAt: /scripts/data"]
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
    """producer → carts → consumer; both sized."""

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

    def write_config(self, name: str, config: dict) -> str:
        (self.root / f"{name}.json").write_text(json.dumps(config), encoding="utf-8")
        return f"{name}.json"

    def write_producer(
        self, *, sizing=PRODUCER_SIZING, targets=("consumer",), config: str | None = None
    ) -> None:
        self.write("producer", produces={"carts": list(targets)}, sizing=sizing, config=config)

    def write_consumer(self, *, sizing=TARGET_SIZING, name="consumer") -> None:
        self.write(name, requires=["carts"], sizing=sizing)

    def size(self, config, target="consumer", **kwargs):
        catalog = load_catalog(self.root)
        return size_producer(catalog.workflows["producer"], target, catalog, config, **kwargs)

    def assertNotEstimable(self, message: str, config, target="consumer") -> None:
        with self.assertRaisesRegex(SizingError, message):
            self.size(config, target)


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


class TargetShapeTests(unittest.TestCase):
    def test_single_scenario_executors(self) -> None:
        self.assertEqual(
            target_shape(iterations(5, vus=2)),
            {"executor": "shared-iterations", "vus": 2, "iterations": 5},
        )
        self.assertEqual(
            target_shape(constant(5, "5m")),
            {"executor": "constant-vus", "vus": 5, "duration": "5m"},
        )
        per_vu = {"scenarios": {"s": {"executor": "per-vu-iterations", "vus": 2, "iterations": 3}}}
        self.assertEqual(
            target_shape(per_vu), {"executor": "per-vu-iterations", "vus": 2, "iterations": 3}
        )

    def test_scenario_defaults_are_k6_defaults(self) -> None:
        self.assertEqual(
            target_shape({"scenarios": {"s": {"executor": "shared-iterations"}}}),
            {"executor": "shared-iterations", "vus": 1, "iterations": 1},
        )

    def test_top_level_shortcuts_follow_k6_rules(self) -> None:
        cases = [
            ({"iterations": 5}, {"executor": "shared-iterations", "vus": 1, "iterations": 5}),
            ({"vus": 2, "iterations": 5, "duration": "1m"},
             {"executor": "shared-iterations", "vus": 2, "iterations": 5}),
            ({"vus": 5, "duration": "5m"}, {"executor": "constant-vus", "vus": 5, "duration": "5m"}),
            ({}, {"executor": "shared-iterations", "vus": 1, "iterations": 1}),
            # k6 ignores vus without iterations or duration: one iteration, one VU.
            ({"vus": 3, "thresholds": {}}, {"executor": "shared-iterations", "vus": 1, "iterations": 1}),
        ]
        for config, shape in cases:
            with self.subTest(config=config):
                self.assertEqual(target_shape(config), shape)

    def test_not_estimable_configs(self) -> None:
        cases = {
            "config declares 2 scenarios; sizing needs exactly one": {
                "scenarios": {"a": {"executor": "shared-iterations"},
                              "b": {"executor": "shared-iterations"}},
            },
            "config declares 0 scenarios; sizing needs exactly one": {"scenarios": {}},
            "config scenario a must be an object": {"scenarios": {"a": 1}},
            'executor "ramping-vus" is not estimable': {
                "scenarios": {"a": {"executor": "ramping-vus", "stages": []}},
            },
            "executor null is not estimable": {"scenarios": {"a": {"vus": 1}}},
            "stages are not estimable": {"stages": [{"duration": "1m", "target": 5}]},
            "invalid iterations 0": {"iterations": 0},
            'invalid iterations "5"': {"iterations": "5"},
            "invalid vus true": {"vus": True, "iterations": 5},
            'invalid duration "5"': {"duration": "5"},
            "invalid duration null": {"scenarios": {"a": {"executor": "constant-vus", "vus": 1}}},
        }
        for message, config in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(SizingError, message):
                target_shape(config)


class SizingMathTests(SizingCase):
    def test_iterations_shape_is_rows_needed(self) -> None:
        plan = self.size(iterations(50))
        self.assertEqual(plan.datasets, ("carts",))
        self.assertEqual(
            dict(plan.shape), {"executor": "shared-iterations", "vus": 1, "iterations": 50}
        )
        self.assertEqual((plan.rows_needed, plan.iterations, plan.vus), (50, 58, 1))
        self.assertEqual(plan.margin, 0.15)
        self.assertEqual(plan.estimated_seconds, 58.0)

    def test_per_vu_iterations_multiply(self) -> None:
        config = {"scenarios": {"s": {"executor": "per-vu-iterations", "vus": 2, "iterations": 3}}}
        self.assertEqual(self.size(config).rows_needed, 6)

    def test_duration_shape_uses_target_pace(self) -> None:
        plan = self.size(constant(5, "5m"))
        # ceil(5 × 300 / 2) = 750; ceil(750 × 1.15) = 863; ceil(863 × 1 / 270) = 4
        self.assertEqual((plan.rows_needed, plan.iterations, plan.vus), (750, 863, 4))
        self.assertEqual(plan.estimated_seconds, 215.75)

    def test_ceilings_use_exact_decimals(self) -> None:
        self.write_consumer(sizing={"margin": 0.1})
        # 20 × 1.1 is 22.000000000000004 in binary floats
        self.assertEqual(self.size(iterations(20)).iterations, 22)

    def test_vus_never_exceed_iterations(self) -> None:
        self.write_producer(sizing={"iterationSeconds": 600, "maxSeconds": 270})
        self.write_consumer(sizing={"margin": 0})
        plan = self.size(iterations(2))
        self.assertEqual((plan.iterations, plan.vus), (2, 2))
        self.assertEqual(plan.estimated_seconds, 600.0)

    def test_duration_needs_target_pace(self) -> None:
        self.write_consumer(sizing={"margin": 0.15})
        self.assertNotEstimable("consumer declares no sizing.iterationSeconds", constant(5, "5m"))
        self.assertEqual(self.size(iterations(5)).rows_needed, 5)

    def test_invalid_configs_are_reasons_not_crashes(self) -> None:
        self.assertNotEstimable("invalid iterations 0", iterations(0))
        self.assertNotEstimable('invalid vus "x"', constant("x", "5m"))
        self.assertNotEstimable('invalid duration "5"', constant(5, "5"))

    def test_rows_needed_ignores_unrelated_options(self) -> None:
        target = load_catalog(self.root).workflows["consumer"]
        config = {**iterations(7), "thresholds": {"checks": ["rate>0.9"]}, "tags": {"x": "y"}}
        self.assertEqual(rows_needed(target, config), 7)

    def test_preset_is_carried(self) -> None:
        self.assertEqual(self.size(iterations(5), preset="5-iterations").preset, "5-iterations")


class SizingEligibilityTests(SizingCase):
    def test_producer_needs_pace_and_budget(self) -> None:
        catalog = load_catalog(self.root)
        self.assertTrue(is_sizable_producer(catalog.workflows["producer"]))
        for sizing in ({"iterationSeconds": 1}, {"maxSeconds": 270}, None):
            with self.subTest(sizing=str(sizing)):
                self.write_producer(sizing=sizing)
                self.assertNotEstimable(
                    "producer is not a sizable producer: needs sizing.iterationSeconds "
                    "and sizing.maxSeconds",
                    iterations(5),
                )

    def test_unknown_and_unsized_targets(self) -> None:
        self.assertNotEstimable("producer does not produce data for nope", iterations(5), "nope")
        self.write_consumer(sizing=None)
        self.assertNotEstimable("consumer declares no spec.sizing", iterations(5))

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


class ProducerConfigTests(SizingCase):
    def test_without_producer_config_uses_shortcuts(self) -> None:
        self.assertEqual(self.size(iterations(50)).config, {"vus": 1, "iterations": 58})

    def test_scenario_keeps_its_name_options_and_max_duration(self) -> None:
        base = {
            "scenarios": {"browser_cart": {
                "executor": "constant-vus", "vus": 9, "duration": "5m", "maxDuration": "4m",
                "options": {"browser": {"type": "chromium"}}, "tags": {"kind": "ui"},
            }},
            "thresholds": {"checks": ["rate>0.95"]},
        }
        self.write_producer(config=self.write_config("producer-config", base))
        self.assertEqual(self.size(iterations(5)).config, {
            "scenarios": {"browser_cart": {
                "maxDuration": "4m", "options": {"browser": {"type": "chromium"}},
                "tags": {"kind": "ui"}, "executor": "shared-iterations", "vus": 1, "iterations": 6,
            }},
            "thresholds": {"checks": ["rate>0.95"]},
        })

    def test_shortcut_base_drops_its_shape(self) -> None:
        base = {"vus": 9, "duration": "5m", "stages": [], "tags": {"suite": "x"}}
        self.write_producer(config=self.write_config("producer-config", base))
        self.assertEqual(
            self.size(iterations(5)).config, {"tags": {"suite": "x"}, "vus": 1, "iterations": 6}
        )

    def test_multi_scenario_producer_config_is_not_sizable(self) -> None:
        base = {"scenarios": {"a": {"executor": "shared-iterations"},
                              "b": {"executor": "shared-iterations"}}}
        self.write_producer(config=self.write_config("producer-config", base))
        self.assertNotEstimable(
            "producer config declares 2 scenarios; sizing needs exactly one", iterations(5)
        )

    def test_write_producer_config(self) -> None:
        plan = self.size(iterations(5))
        path = write_producer_config(plan, self.root / "state")
        self.assertEqual(path, self.root / "state" / "k6-config-producer.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"vus": 1, "iterations": 6})


class SizingTextTests(SizingCase):
    def test_summary_lines_with_preset(self) -> None:
        plan = self.size(iterations(5), preset="5-iterations")
        self.assertEqual(summary_lines(plan), [
            "[punch] sizing producer for consumer (5-iterations)",
            "  rows needed   : 5  (shared-iterations vus=1 iterations=5)",
            "  margin 15%   : 6 producer iterations",
            "  producer VUS  : 1  (~6s of 270s budget)",
        ])

    def test_summary_lines_without_preset_show_the_shape(self) -> None:
        lines = summary_lines(self.size(constant(5, "5m")))
        self.assertEqual(lines[0], "[punch] sizing producer for consumer (constant-vus vus=5 duration=5m)")
        self.assertEqual(lines[3], "  producer VUS  : 4  (~216s of 270s budget)")

    def test_fractional_margin_and_budget(self) -> None:
        self.write_producer(sizing={"iterationSeconds": 1, "maxSeconds": 90.5})
        self.write_consumer(sizing={"margin": 0.125})
        lines = summary_lines(self.size(iterations(8)))
        self.assertEqual(lines[2], "  margin 12.5%   : 9 producer iterations")
        self.assertEqual(lines[3], "  producer VUS  : 1  (~9s of 90.5s budget)")

    def test_budget_warning_when_one_iteration_exceeds_it(self) -> None:
        self.assertEqual(len(summary_lines(self.size(iterations(5)))), 4)
        self.write_producer(sizing={"iterationSeconds": 600, "maxSeconds": 270})
        self.assertEqual(
            summary_lines(self.size(iterations(5)))[-1],
            "[punch] warning: one producer iteration exceeds its 270s budget",
        )

    def test_input_warnings_name_small_inputs(self) -> None:
        plan = self.size(iterations(5))  # 6 producer iterations
        self.assertEqual(input_warnings(plan, {"carts": 5, "users": 6, "x": 1}), [
            '[punch] warning: producer reads "carts" (5 rows) but runs 6 iterations; rows repeat',
            '[punch] warning: producer reads "x" (1 row) but runs 6 iterations; rows repeat',
        ])

    def test_shortfall_compares_against_rows_needed(self) -> None:
        plan = self.size(iterations(5))
        self.assertEqual(shortfall(plan, {"carts": 4}),
                         ['[punch] warning: "carts" has 4 rows; consumer needs 5'])
        self.assertEqual(shortfall(plan, {"carts": 5}), [])
        self.assertEqual(shortfall(plan, {}),
                         ['[punch] warning: "carts" has 0 rows; consumer needs 5'])

    def test_next_hint_names_target_and_shape(self) -> None:
        self.assertEqual(
            next_hint(self.size(iterations(5), preset="5-iterations"), {"carts": 6}),
            "[punch] carts ready (6 rows); run consumer next with 5-iterations.",
        )
        self.assertEqual(
            next_hint(self.size(iterations(5)), {"carts": 1}),
            "[punch] carts ready (1 row); run consumer next with shared-iterations vus=1 iterations=5.",
        )

    def test_sizing_evidence(self) -> None:
        plan = self.size(iterations(5), preset="5-iterations")
        self.assertEqual(sizing_evidence(plan, {"carts": 4}), {
            "target": "consumer",
            "datasets": ["carts"],
            "shape": {"executor": "shared-iterations", "vus": 1, "iterations": 5},
            "preset": "5-iterations",
            "rowsNeeded": 5,
            "margin": 0.15,
            "producerIterations": 6,
            "producerVus": 1,
            "producedRows": {"carts": 4},
            "short": True,
        })
        self.assertFalse(sizing_evidence(plan, {"carts": 6})["short"])


if __name__ == "__main__":
    unittest.main()

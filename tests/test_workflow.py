from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.workflow import AUTO, data_label, first_jobs, next_jobs, plan_workflow, step_label

# The parent repository's http-cart, http-orders, and http-orders-status values.
CART_SIZING = {"iterationSeconds": 1.1, "maxSeconds": 270}
ORDERS_SIZING = {"iterationSeconds": 1.1, "maxSeconds": 270, "margin": 0.15}
STATUS_SIZING = {"iterationSeconds": 0.6, "margin": 0.15}
ITERATIONS_5 = {
    "scenarios": {
        "default": {
            "executor": "shared-iterations",
            "vus": 1,
            "iterations": 5,
            "maxDuration": "5m",
        }
    }
}
CONSTANT_5_VUS_5M = {
    "scenarios": {"default": {"executor": "constant-vus", "vus": 5, "duration": "5m"}}
}


def job_yaml(
    name: str,
    *,
    produces: dict[str, list[str]] | None = None,
    requires: list[str] | None = None,
    optional: list[str] | None = None,
    sizing: dict | None = None,
    config: str | None = None,
    directory: str = "data",
    forward: list[str] | None = None,
    summary: str | None = None,
) -> str:
    lines = [
        "apiVersion: punch/v1",
        "kind: K6Job",
        "metadata:",
        f"  name: {name}",
        "spec:",
        "  workingDirectory: .",
        "  compose:",
        "    file: docker-compose.yml",
        "    service: k6",
        "  k6:",
        f"    script: /scripts/{name}.js",
    ]
    if config is not None:
        lines.append(f"    config: {config}")
    if forward:
        lines += ["  environment:", f"    forward: [{', '.join(forward)}]"]
    if summary is not None:
        lines += ["  outputs:", "    summary:", f"      path: {summary}"]
    if produces or requires or optional:
        lines += ["  data:", f"    directory: {directory}", "    mountedAt: /scripts/data"]
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
        if optional:
            lines.append(f"    optional: [{', '.join(optional)}]")
    if sizing is not None:
        lines.append("  sizing:")
        lines += [f"    {key}: {value}" for key, value in sizing.items()]
    return "\n".join(lines) + "\n"


class WorkflowCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "docker-compose.yml").touch()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, **kwargs) -> None:
        (self.root / f"{name}.yaml").write_text(job_yaml(name, **kwargs), encoding="utf-8")

    def write_options(self, name: str, config: dict) -> Path:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_chain(self) -> None:
        """cart writes carts for orders; orders writes orders for status."""
        self.write("cart", produces={"carts": ["orders"]}, sizing=CART_SIZING)
        self.write(
            "orders", requires=["carts"], produces={"orders": ["status"]}, sizing=ORDERS_SIZING
        )
        self.write("status", requires=["orders"], sizing=STATUS_SIZING)

    def catalog(self):
        return load_catalog(self.root)


class CandidateTests(WorkflowCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("audit", optional=["carts"])
        self.write("cart", produces={"carts": ["audit", "orders"]})
        self.write("lonely")
        self.write("orders", requires=["carts"], produces={"orders": ["receipt", "status"]})
        self.write("pay", produces={"payments": ["receipt"]})
        self.write("receipt", requires=["orders", "payments"])
        self.write("status", requires=["orders"])

    def test_first_jobs_need_no_earlier_step_and_have_a_next_job(self) -> None:
        # audit writes nothing, lonely has no data, pay's only reader needs orders too.
        self.assertEqual(first_jobs(self.catalog()), ("cart",))

    def test_next_jobs_follow_the_last_step_in_catalog_order(self) -> None:
        self.assertEqual(
            next_jobs(["cart"], self.catalog()),
            (("audit", ("carts",)), ("orders", ("carts",))),
        )

    def test_next_jobs_need_every_required_dataset_written(self) -> None:
        catalog = self.catalog()
        self.assertEqual(next_jobs(["cart", "orders"], catalog), (("status", ("orders",)),))
        self.assertEqual(
            next_jobs(["pay", "cart", "orders"], catalog),
            (("receipt", ("orders",)), ("status", ("orders",))),
        )

    def test_next_jobs_never_repeat_a_job(self) -> None:
        self.write("ping", optional=["pongs"], produces={"pings": ["pong"]})
        self.write("pong", optional=["pings"], produces={"pongs": ["ping"]})
        catalog = self.catalog()
        self.assertEqual(next_jobs(["ping"], catalog), (("pong", ("pings",)),))
        self.assertEqual(next_jobs(["ping", "pong"], catalog), ())

    def test_nothing_follows_an_empty_or_finished_workflow(self) -> None:
        catalog = self.catalog()
        self.assertEqual(next_jobs([], catalog), ())
        self.assertEqual(next_jobs(["cart", "orders", "status"], catalog), ())


class PlanTests(WorkflowCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_chain()
        self.iterations_5 = self.write_options("5-iterations", ITERATIONS_5)
        self.constant_5m = self.write_options("5-vu-5m", CONSTANT_5_VUS_5M)

    def plan(self, loads, jobs=("cart", "orders", "status")):
        return plan_workflow(list(jobs), list(loads), self.catalog())

    def test_each_earlier_step_is_sized_for_the_next(self) -> None:
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.warnings, [])
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            [
                "auto: sized for step 2 (7 iterations, 1 VU)",
                "auto: sized for step 3 (6 iterations, 1 VU)",
                "5-iterations",
            ],
        )
        cart, orders, _status = plan.steps
        # status reads 5 rows; orders runs ceil(5 x 1.15) = 6; cart ceil(6 x 1.15) = 7.
        self.assertEqual((orders.sizing.rows_needed, cart.sizing.rows_needed), (5, 6))
        self.assertEqual(cart.config, {"vus": 1, "iterations": 7})
        self.assertEqual((cart.rows_needed, orders.rows_needed), ({"carts": 6}, {"orders": 5}))

    def test_a_duration_load_sizes_both_producers(self) -> None:
        plan = self.plan([AUTO, AUTO, self.constant_5m])
        self.assertEqual(plan.errors, [])
        # status reads ceil(5 x 300 / 0.6) = 2500 rows.
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            [
                "auto: sized for step 2 (3307 iterations, 14 VUs)",
                "auto: sized for step 3 (2875 iterations, 12 VUs)",
                "5-vu-5m",
            ],
        )
        self.assertEqual(plan.steps[1].rows_needed, {"orders": 2500})

    def test_each_read_links_to_its_writer_and_writers_produce_only_what_is_read(self) -> None:
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual([step.reads for step in plan.steps], [{}, {"carts": 1}, {"orders": 2}])
        self.assertEqual([step.produce for step in plan.steps], [("carts",), ("orders",), ()])
        self.assertEqual(
            [data_label(step) for step in plan.steps],
            [
                "writes carts",
                "reads carts from step 1; writes orders",
                "reads orders from step 2",
            ],
        )

    def test_a_dataset_no_later_step_reads_is_not_produced(self) -> None:
        plan = self.plan([AUTO, self.iterations_5], jobs=("cart", "orders"))
        self.assertEqual([step.produce for step in plan.steps], [("carts",), ()])

    def test_without_sizing_every_step_runs_the_given_file(self) -> None:
        plan = self.plan([self.iterations_5] * 3)
        self.assertEqual(plan.errors, [])
        self.assertEqual([step.sizing for step in plan.steps], [None, None, None])
        self.assertEqual([step.config_path for step in plan.steps], [self.iterations_5] * 3)
        self.assertEqual([step_label(step) for step in plan.steps], ["5-iterations"] * 3)

    def test_a_job_default_load_is_never_sized(self) -> None:
        self.write_options("own", ITERATIONS_5)
        self.write("cart", produces={"carts": ["orders"]}, sizing=CART_SIZING, config="own.json")
        plan = self.plan([None, None, None])
        self.assertEqual(plan.errors, [])
        self.assertEqual([step.sizing for step in plan.steps], [None, None, None])
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            ["job default (own)", "script options", "script options"],
        )

    def test_an_auto_step_without_a_reader_runs_its_own_config(self) -> None:
        plan = self.plan([AUTO, AUTO, AUTO])
        # status's script options run 1 iteration: orders ceil(1 x 1.15) = 2, cart 3.
        self.assertEqual(
            [step_label(step) for step in plan.steps],
            [
                "auto: sized for step 2 (3 iterations, 1 VU)",
                "auto: sized for step 3 (2 iterations, 1 VU)",
                "script options",
            ],
        )

    def test_a_pair_punch_cannot_size_warns_and_runs_the_job_config(self) -> None:
        self.write("orders", requires=["carts"], produces={"orders": ["status"]})
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(plan.errors, [])
        self.assertEqual(
            plan.warnings,
            [
                "step 2 orders: not sized for step 3 (orders is not a sizable producer: needs "
                "sizing.iterationSeconds and sizing.maxSeconds); it runs its default config",
                "step 1 cart: not sized for step 2 (orders declares no spec.sizing); "
                "it runs its default config",
            ],
        )
        self.assertEqual(
            [step_label(step) for step in plan.steps[:2]], ["script options", "script options"]
        )

    def test_two_readers_of_one_writer_reuse_its_rows(self) -> None:
        self.write("cart", produces={"carts": ["orders", "audit"]}, sizing=CART_SIZING)
        self.write("audit", requires=["carts"])
        plan = self.plan([AUTO, AUTO, self.iterations_5], jobs=("cart", "orders", "audit"))
        self.assertEqual(plan.errors, [])
        self.assertIn("steps 2 and 3 read carts from step 1; its rows are reused", plan.warnings)

    def test_an_optional_dataset_without_an_earlier_writer_uses_built_in_data(self) -> None:
        self.write("audit", optional=["carts"], produces={"audits": ["report"]})
        self.write("report", requires=["audits"])
        plan = self.plan([AUTO, self.iterations_5], jobs=("audit", "report"))
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.steps[0].built_in, ("carts",))
        self.assertEqual(data_label(plan.steps[0]), "carts: built-in data; writes audits")

    def test_a_writer_outside_the_reader_data_directory_is_an_error(self) -> None:
        self.write("status", requires=["orders"], sizing=STATUS_SIZING, directory="other")
        plan = self.plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(
            plan.errors,
            [
                f"step 3 status reads orders from step 2, but {self.root / 'data' / 'orders.csv'} "
                f"is outside its data directory {self.root / 'other'}"
            ],
        )

    def test_an_unreadable_options_file_is_an_error(self) -> None:
        missing = self.root / "missing.json"
        plan = self.plan([AUTO, AUTO, missing])
        [error] = plan.errors
        self.assertTrue(error.startswith(f"step 3 status: could not read k6 config {missing}"))

    def test_bad_requests_are_errors_not_exceptions(self) -> None:
        catalog = self.catalog()
        cases = {
            "no jobs": (
                [],
                [],
                "a workflow needs at least one job and one load per job; got 0 jobs and 0 loads",
            ),
            "lengths differ": (
                ["cart"],
                [AUTO, AUTO],
                "a workflow needs at least one job and one load per job; got 1 job and 2 loads",
            ),
            "unknown job": (["cart", "nope"], [AUTO, AUTO], 'step 2: unknown job "nope"'),
            "repeated job": (
                ["cart", "orders", "cart"],
                [AUTO, AUTO, AUTO],
                "step 3 cart repeats step 1",
            ),
            "unknown load": (
                ["cart"],
                ["5-iterations"],
                "step 1 cart: unknown load '5-iterations'",
            ),
            "missing writer": (
                ["orders", "status"],
                [AUTO, AUTO],
                "step 1 orders reads carts; no earlier step writes it. Put cart before step 1",
            ),
        }
        for case, (jobs, loads, error) in cases.items():
            with self.subTest(case=case):
                plan = plan_workflow(jobs, loads, catalog)
                self.assertEqual(plan.errors, [error])


if __name__ == "__main__":
    unittest.main()

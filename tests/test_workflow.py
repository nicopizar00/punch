from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.execution import DatasetResult, ExecutionResult
from punch.workflow import (
    AUTO,
    data_label,
    first_jobs,
    next_jobs,
    plan_workflow,
    run_workflow,
    step_label,
)

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


class RunTests(WorkflowCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_chain()
        self.iterations_5 = self.write_options("5-iterations", ITERATIONS_5)
        self.state_dir = self.root / "state"
        self.calls: list[tuple[str, dict]] = []
        # job name -> (k6 exit code or None, passed, rows written) or "interrupt"
        self.outcomes: dict = {}

    def passing(self) -> dict:
        return {
            "cart": (0, True, {"carts": 7}),
            "orders": (0, True, {"orders": 6}),
            "status": (0, True, {}),
        }

    def fake_execute(self, job, **kwargs) -> ExecutionResult:
        self.calls.append((job.name, kwargs))
        outcome = self.outcomes.get(job.name, (0, True, {}))
        if outcome == "interrupt":
            raise KeyboardInterrupt
        code, passed, rows = outcome
        datasets = tuple(
            DatasetResult(dataset, self.root / "data" / f"{dataset}.csv", count, passed)
            for dataset, count in rows.items()
        )
        failure = None if passed else "k6 failed"
        return ExecutionResult(job.name, ("docker",), code, passed, failure, datasets)

    def run_plan(self, loads, jobs=("cart", "orders", "status"), stdin=None):
        plan = plan_workflow(list(jobs), list(loads), self.catalog())
        self.assertEqual(plan.errors, [])
        output = io.StringIO()
        with patch("punch.workflow.execute_job", side_effect=self.fake_execute):
            code = run_workflow(
                plan,
                self.catalog(),
                environment={"PATH": "/bin"},
                base_urls={"k6": "http://app"},
                state_dir=self.state_dir,
                stdin=stdin if stdin is not None else io.StringIO(),
                stdout=output,
            )
        return code, output.getvalue(), self.evidence()

    def evidence(self) -> dict:
        return json.loads((self.state_dir / "punch-workflow.json").read_text(encoding="utf-8"))

    def test_runs_every_step_in_order_with_its_data_wiring(self) -> None:
        self.outcomes = self.passing()
        code, text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(code, 0)
        self.assertEqual([name for name, _ in self.calls], ["cart", "orders", "status"])
        cart, orders, status = (kwargs for _, kwargs in self.calls)
        data = self.root / "data"
        self.assertEqual(
            (cart["produce"], cart["data_overrides"], cart["optional_choices"]),
            (("carts",), {}, {}),
        )
        self.assertEqual(orders["data_overrides"], {"carts": data / "carts.csv"})
        self.assertEqual(orders["produce"], ("orders",))
        self.assertEqual(status["data_overrides"], {"orders": data / "orders.csv"})
        self.assertEqual(status["produce"], ())
        self.assertEqual(cart["config"], self.state_dir / "k6-config-cart.json")
        self.assertEqual(orders["config"], self.state_dir / "k6-config-orders.json")
        self.assertEqual(status["config"], self.iterations_5)
        self.assertTrue(all(kwargs["docker_run_confirmed"] for _, kwargs in self.calls))
        self.assertEqual(
            json.loads((self.state_dir / "k6-config-cart.json").read_text(encoding="utf-8")),
            {"vus": 1, "iterations": 7},
        )
        self.assertEqual(evidence["version"], 1)
        self.assertEqual((evidence["outcome"], evidence["passed"]), ("passed", True))
        self.assertEqual(evidence["jobs"], ["cart", "orders", "status"])
        self.assertEqual([step["status"] for step in evidence["steps"]], ["passed"] * 3)
        self.assertEqual(
            [step["rows"] for step in evidence["steps"]], [{"carts": 7}, {"orders": 6}, {}]
        )
        self.assertEqual(evidence["steps"][0]["sizing"]["producerIterations"], 7)
        self.assertEqual(evidence["steps"][0]["sizing"]["producedRows"], {"carts": 7})
        self.assertIsNone(evidence["steps"][2]["sizing"])
        self.assertEqual(evidence["steps"][1]["reads"], {"carts": 1})
        self.assertEqual(
            evidence["steps"][0]["label"], "auto: sized for step 2 (7 iterations, 1 VU)"
        )
        self.assertEqual(evidence["warnings"], [])
        self.assertIn("[punch] evidence written: ", text)

    def test_a_failed_step_stops_the_workflow_with_its_k6_exit_code(self) -> None:
        self.outcomes = {**self.passing(), "orders": (99, False, {})}
        code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(code, 99)
        self.assertEqual([name for name, _ in self.calls], ["cart", "orders"])
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["passed", "failed", "skipped"]
        )
        self.assertEqual(
            (evidence["steps"][1]["exitCode"], evidence["steps"][1]["failure"]), (99, "k6 failed")
        )
        self.assertEqual((evidence["outcome"], evidence["passed"]), ("failed", False))

    def test_a_step_that_fails_after_k6_exits_0_still_exits_1(self) -> None:
        self.outcomes = {"cart": (0, False, {})}
        code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(code, 1)
        self.assertEqual(evidence["outcome"], "failed")

    def test_a_step_that_never_starts_docker_exits_1_without_a_delete_prompt(self) -> None:
        self.outcomes = {"cart": (None, False, {})}
        (self.root / "data").mkdir()
        (self.root / "data" / "carts.csv").write_text("id\n1\n", encoding="utf-8")
        code, text, evidence = self.run_plan(
            [AUTO, AUTO, self.iterations_5], stdin=_Terminal("y\n")
        )
        self.assertEqual(code, 1)
        self.assertEqual([name for name, _ in self.calls], ["cart"])
        self.assertNotIn("Delete consumed", text)
        self.assertTrue((self.root / "data" / "carts.csv").exists())
        self.assertIsNone(evidence["steps"][0]["exitCode"])
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["failed", "skipped", "skipped"]
        )

    def test_an_interrupt_marks_the_step_and_skips_the_rest(self) -> None:
        self.outcomes = {**self.passing(), "orders": "interrupt"}
        plan = plan_workflow(
            ["cart", "orders", "status"], [AUTO, AUTO, self.iterations_5], self.catalog()
        )
        with patch("punch.workflow.execute_job", side_effect=self.fake_execute):
            with self.assertRaises(KeyboardInterrupt):
                run_workflow(
                    plan,
                    self.catalog(),
                    environment={},
                    base_urls={},
                    state_dir=self.state_dir,
                    stdin=io.StringIO(),
                    stdout=io.StringIO(),
                )
        evidence = self.evidence()
        self.assertEqual((evidence["outcome"], evidence["passed"]), ("interrupted", False))
        self.assertEqual(
            [step["status"] for step in evidence["steps"]], ["passed", "interrupted", "skipped"]
        )

    def test_a_writer_short_of_rows_warns_and_the_workflow_continues(self) -> None:
        self.outcomes = {**self.passing(), "cart": (0, True, {"carts": 4})}
        code, text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        warning = "step 1 cart wrote 4 carts rows; step 2 reads 6, so rows repeat"
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 3)
        self.assertIn(f"[punch] warning: {warning}\n", text)
        self.assertEqual(evidence["warnings"], [warning])

    def test_optional_reads_use_the_writer_file_and_unwritten_ones_built_in_data(self) -> None:
        self.write("cart", produces={"carts": ["audit", "orders"]}, sizing=CART_SIZING)
        self.write("audit", optional=["carts"])
        self.outcomes = {"cart": (0, True, {"carts": 5})}
        self.run_plan([self.iterations_5, self.iterations_5], jobs=("cart", "audit"))
        audit = dict(self.calls)["audit"]
        self.assertEqual(audit["optional_choices"], {"carts": True})
        self.assertEqual(audit["data_overrides"], {"carts": self.root / "data" / "carts.csv"})
        self.calls.clear()
        self.run_plan([self.iterations_5], jobs=("audit",))
        self.assertEqual(dict(self.calls)["audit"]["optional_choices"], {"carts": False})

    def test_base_url_reaches_only_the_jobs_that_forward_it(self) -> None:
        self.write("status", requires=["orders"], sizing=STATUS_SIZING, forward=["BASE_URL"])
        self.outcomes = self.passing()
        self.run_plan([AUTO, AUTO, self.iterations_5])
        environments = {name: kwargs["environment"] for name, kwargs in self.calls}
        self.assertEqual(environments["cart"], {"PATH": "/bin"})
        self.assertEqual(environments["status"], {"PATH": "/bin", "BASE_URL": "http://app"})

    def test_a_passing_step_prints_its_metrics(self) -> None:
        self.write("status", requires=["orders"], sizing=STATUS_SIZING, summary="status.json")
        (self.root / "status.json").write_text(
            json.dumps(
                {
                    "totalRequests": 5,
                    "errorRate": 0,
                    "p90Ms": 12.5,
                    "checkPassRate": 1,
                    "durationMs": 1500,
                }
            ),
            encoding="utf-8",
        )
        self.outcomes = self.passing()
        _code, text, _evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertIn("[punch] status metrics:\n  requests    : 5\n", text)
        self.assertIn("  duration    : 1.5s\n", text)

    def test_the_written_datasets_are_offered_for_deletion_once(self) -> None:
        self.outcomes = self.passing()
        data = self.root / "data"
        data.mkdir()
        for dataset in ("carts", "orders"):
            (data / f"{dataset}.csv").write_text("id\n1\n", encoding="utf-8")
        code, text, _evidence = self.run_plan(
            [AUTO, AUTO, self.iterations_5], stdin=_Terminal("n\nn\n")
        )
        self.assertEqual(code, 0)
        self.assertEqual(text.count('Delete consumed "carts" data'), 1)
        self.assertEqual(text.count('Delete consumed "orders" data'), 1)
        self.assertTrue((data / "carts.csv").exists())

    def test_evidence_from_an_earlier_run_is_replaced_before_step_1(self) -> None:
        self.state_dir.mkdir()
        (self.state_dir / "punch-workflow.json").write_text(
            '{"outcome": "passed", "stale": true}', encoding="utf-8"
        )
        self.outcomes = self.passing()
        seen: list[dict] = []
        original = self.fake_execute

        def watch(job, **kwargs) -> ExecutionResult:
            if job.name == "cart":
                seen.append(self.evidence())
            return original(job, **kwargs)

        self.fake_execute = watch
        self.run_plan([AUTO, AUTO, self.iterations_5])
        [at_start] = seen
        self.assertEqual(at_start["outcome"], "running")
        self.assertNotIn("stale", at_start)
        self.assertEqual({step["status"] for step in at_start["steps"]}, {"pending"})

    def test_evidence_paths_are_relative_beneath_the_current_directory(self) -> None:
        self.outcomes = self.passing()
        with patch.object(Path, "cwd", return_value=self.root):
            _code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(evidence["steps"][0]["config"], "state/k6-config-cart.json")
        self.assertEqual(evidence["steps"][0]["load"], "auto")
        self.assertEqual(evidence["steps"][2]["load"], "5-iterations.json")
        self.calls.clear()
        _code, _text, evidence = self.run_plan([AUTO, AUTO, self.iterations_5])
        self.assertEqual(
            evidence["steps"][0]["config"], str(self.state_dir / "k6-config-cart.json")
        )


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


if __name__ == "__main__":
    unittest.main()

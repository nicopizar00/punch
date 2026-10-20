from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.workflow import first_jobs, next_jobs

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


if __name__ == "__main__":
    unittest.main()

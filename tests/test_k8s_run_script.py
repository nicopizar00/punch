"""k8s/k6-run.sh: the shell step of the punch-k6-run WorkflowTemplate.

Runs the script under `sh` with a fake `k6` on PATH, as the template runs it
inside the consumer's k6 image (Alpine busybox).
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parents[1] / "k8s" / "k6-run.sh"

FAKE_K6 = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_K6_ARGS"
env > "$FAKE_K6_ENV"
cat "$FAKE_K6_STDOUT"
cat "$FAKE_K6_STDERR" >&2
exit "$FAKE_K6_EXIT"
"""


class K6RunScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        fake = self.bin / "k6"
        fake.write_text(FAKE_K6)
        fake.chmod(0o755)
        self.data = root / "data"
        self.data.mkdir()
        self.out = root / "out"
        self.stdout_file = root / "k6.stdout"
        self.stderr_file = root / "k6.stderr"
        self.args_file = root / "k6.args"
        self.env_file = root / "k6.env"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_script(
        self, *, stdout: str = "", stderr: str = "", k6_exit: int = 0, **params: str
    ) -> subprocess.CompletedProcess[str]:
        self.stdout_file.write_text(stdout)
        self.stderr_file.write_text(stderr)
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "PUNCH_JOB": "http-orders",
            "PUNCH_SCRIPT": "/scripts/scenarios/http-orders/http-orders.js",
            "PUNCH_DATA_DIR": str(self.data),
            "PUNCH_OUT_DIR": str(self.out),
            "PUNCH_OPTIONS_DIR": "/punch/options",
            "FAKE_K6_STDOUT": str(self.stdout_file),
            "FAKE_K6_STDERR": str(self.stderr_file),
            "FAKE_K6_ARGS": str(self.args_file),
            "FAKE_K6_ENV": str(self.env_file),
            "FAKE_K6_EXIT": str(k6_exit),
        }
        env.update({f"PUNCH_{key.upper()}": value for key, value in params.items()})
        return subprocess.run(
            ["sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
        )

    def k6_args(self) -> list[str]:
        return self.args_file.read_text().splitlines()

    def k6_ran(self) -> bool:
        return self.args_file.exists()

    # --- run -------------------------------------------------------------

    def test_runs_the_script_with_the_preset_config(self) -> None:
        result = self.run_script(config="5-iterations")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.k6_args(),
            [
                "run",
                "/scripts/scenarios/http-orders/http-orders.js",
                "--config",
                "/punch/options/5-iterations.json",
            ],
        )

    def test_without_config_k6_gets_no_config_flag(self) -> None:
        self.assertEqual(self.run_script().returncode, 0)
        self.assertNotIn("--config", self.k6_args())

    def test_forwarded_extra_env_becomes_k6_env_flags(self) -> None:
        result = self.run_script(
            forward="EXPECT_TEMPERATURE ORDER_COOL_DOWN_SECONDS",
            extra_env="EXPECT_TEMPERATURE=hot",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.k6_args()[-2:], ["-e", "EXPECT_TEMPERATURE=hot"])

    def test_k6_failure_keeps_its_exit_code_and_publishes_nothing(self) -> None:
        result = self.run_script(
            produces="orders=orderId", produce="orders", stdout="[DATA orders] o-1\n", k6_exit=99
        )
        self.assertEqual(result.returncode, 99)
        self.assertFalse((self.out / "datasets").exists())

    # --- validation (exit 2, before k6) -----------------------------------

    def test_extra_env_outside_forward_exits_2_before_k6(self) -> None:
        result = self.run_script(forward="EXPECT_TEMPERATURE", extra_env="USERS=5")
        self.assertEqual(result.returncode, 2)
        self.assertIn('does not forward "USERS"', result.stderr)
        self.assertFalse(self.k6_ran())

    def test_extra_env_value_with_spaces_exits_2_before_k6(self) -> None:
        result = self.run_script(
            forward="EXPECT_TEMPERATURE", extra_env="EXPECT_TEMPERATURE=very hot"
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.k6_ran())

    def test_undeclared_produce_exits_2_before_k6(self) -> None:
        result = self.run_script(produces="orders=orderId", produce="carts")
        self.assertEqual(result.returncode, 2)
        self.assertIn('does not produce "carts"', result.stderr)
        self.assertFalse(self.k6_ran())

    # --- preflight (exit 1, before k6) -------------------------------------

    def test_missing_required_dataset_fails_preflight_naming_producers(self) -> None:
        result = self.run_script(requires="carts", requires_hint="carts=browser-cart,http-cart")
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            'http-orders requires "carts"; produce it with: browser-cart, http-cart '
            "(--produce carts)",
            result.stderr,
        )
        self.assertFalse(self.k6_ran())

    def test_missing_dataset_without_hint_names_no_known_job(self) -> None:
        result = self.run_script(requires="carts")
        self.assertIn("produce it with: no known job (--produce carts)", result.stderr)

    def test_header_only_dataset_fails_preflight(self) -> None:
        (self.data / "carts.csv").write_text("cartId,productId,sid\n\n")
        result = self.run_script(requires="carts", requires_hint="carts=http-cart")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.k6_ran())

    def test_dataset_without_trailing_newline_passes_preflight(self) -> None:
        (self.data / "carts.csv").write_text("cartId,productId,sid\nc1,p1,s1")
        result = self.run_script(requires="carts")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_required_dataset_path_is_exported(self) -> None:
        (self.data / "auth-tokens.csv").write_text("username,authToken\nu,t\n")
        self.assertEqual(self.run_script(requires="auth-tokens").returncode, 0)
        env = self.env_file.read_text().splitlines()
        self.assertIn(f"DATA_AUTH_TOKENS_CSV={self.data / 'auth-tokens.csv'}", env)

    def test_more_than_one_required_dataset_exits_2_before_k6(self) -> None:
        # The template downloads exactly one dataset file (datasets/<requires>.csv).
        (self.data / "carts.csv").write_text("cartId,productId,sid\nc1,p1,s1\n")
        (self.data / "orders.csv").write_text("orderId\no-1\n")
        result = self.run_script(requires="carts orders")
        self.assertEqual(result.returncode, 2)
        self.assertIn("one required dataset", result.stderr)
        self.assertFalse(self.k6_ran())

    # --- harvest -----------------------------------------------------------

    def test_harvests_opted_in_dataset(self) -> None:
        result = self.run_script(
            produces="orders=orderId",
            produce="orders",
            stdout="iteration 1\n[DATA orders] o-1\n[DATA orders]o-2\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.out / "datasets" / "orders.csv").read_text(), "orderId\no-1\no-2\n")
        self.assertEqual((self.out / "rows.txt").read_text().strip(), "orders=2")
        self.assertIn("[DATA orders] o-1", result.stdout)

    def test_stderr_records_are_never_harvested(self) -> None:
        result = self.run_script(
            produces="orders=orderId",
            produce="orders",
            stdout="[DATA orders] o-1\n",
            stderr="[DATA orders] o-from-stderr\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.out / "datasets" / "orders.csv").read_text(), "orderId\no-1\n")

    def test_without_produce_nothing_is_published(self) -> None:
        result = self.run_script(produces="orders=orderId", stdout="[DATA orders] o-1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "datasets").exists())
        self.assertEqual((self.out / "rows.txt").read_text(), "")

    def test_produce_all_opts_into_every_declared_dataset(self) -> None:
        result = self.run_script(
            produces="carts=cartId,productId,sid orders=orderId",
            produce="all",
            stdout="[DATA carts] c1,p1,s1\n[DATA orders] o-1\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(path.name for path in (self.out / "datasets").iterdir()),
            ["carts.csv", "orders.csv"],
        )

    def test_undeclared_record_fails_even_without_produce(self) -> None:
        result = self.run_script(produces="orders=orderId", stdout="[DATA carts] c1,p1,s1\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn('undeclared dataset "carts"', result.stderr)

    def test_column_mismatch_fails_and_publishes_nothing(self) -> None:
        result = self.run_script(
            produces="carts=cartId,productId,sid",
            produce="carts",
            stdout="[DATA carts] c1,p1,s1\n[DATA carts] c2,p2\n",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected 3 columns", result.stderr)
        self.assertFalse((self.out / "datasets").exists())

    def test_quoted_comma_payload_fails_loudly(self) -> None:
        result = self.run_script(
            produces="carts=cartId,productId,sid",
            produce="carts",
            stdout='[DATA carts] "c,1",p1,s1\n',
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.out / "datasets").exists())

    def test_zero_rows_for_an_opted_in_dataset_fails(self) -> None:
        result = self.run_script(produces="orders=orderId", produce="orders", stdout="none\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no [DATA orders] records were produced", result.stderr)
        self.assertFalse((self.out / "datasets").exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "k6-wrapper.sh"
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"

FAKE_K6 = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_K6_ARGS"]).write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
"""


class K6WrapperTests(unittest.TestCase):
    def _run_wrapper(self, args: list[str], *, scenario: str | None, tmp_dir: Path) -> subprocess.CompletedProcess[str]:
        fake_k6 = tmp_dir / "k6"
        fake_k6.write_text(FAKE_K6, encoding="utf-8")
        fake_k6.chmod(fake_k6.stat().st_mode | stat.S_IEXEC)

        captured_args_path = tmp_dir / "captured-args.txt"

        env = dict(os.environ)
        env["PATH"] = f"{tmp_dir}{os.pathsep}{env['PATH']}"
        env["FAKE_K6_ARGS"] = str(captured_args_path)
        if scenario is not None:
            env["SCENARIO"] = scenario
        else:
            env.pop("SCENARIO", None)

        result = subprocess.run(
            ["sh", str(WRAPPER), *args],
            env=env,
            capture_output=True,
            text=True,
        )
        result.captured_args_path = captured_args_path  # type: ignore[attr-defined]
        return result

    def test_scenario_smoke_replaces_positional_script_arg(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/ignored.js"], scenario="smoke", tmp_dir=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = result.captured_args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(captured, ["run", "/scripts/smoke.js"])

    def test_scenario_gate_maps_to_catalog_gate_script(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/ignored.js"], scenario="gate", tmp_dir=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = result.captured_args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(captured, ["run", "/scripts/catalog-gate.js"])

    def test_scenario_journey_maps_to_order_journey_script(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/ignored.js"], scenario="journey", tmp_dir=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = result.captured_args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(captured, ["run", "/scripts/order-journey.js"])

    def test_scenario_bff_checkout_journey_maps_to_its_script(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/ignored.js"], scenario="bff-checkout-journey", tmp_dir=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = result.captured_args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(captured, ["run", "/scripts/bff-checkout-journey.js"])

    def test_no_scenario_passes_args_through_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/smoke.js"], scenario=None, tmp_dir=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = result.captured_args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(captured, ["run", "/scripts/smoke.js"])

    def test_scenario_with_no_positional_args_defaults_to_run(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper([], scenario="smoke", tmp_dir=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = result.captured_args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(captured, ["run", "/scripts/smoke.js"])

    def test_unknown_scenario_fails_before_invoking_k6(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/smoke.js"], scenario="bogus", tmp_dir=Path(tmp))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown SCENARIO", result.stderr)
            self.assertFalse(result.captured_args_path.exists())

    def test_prints_punch_banner(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/smoke.js"], scenario=None, tmp_dir=Path(tmp))
            self.assertIn("👊", result.stderr)
            self.assertIn("Punch", result.stderr)

    def test_prints_success_before_launching_k6(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/smoke.js"], scenario=None, tmp_dir=Path(tmp))
            self.assertIn("✅", result.stderr)

    def test_unknown_scenario_error_uses_error_emoji(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/smoke.js"], scenario="bogus", tmp_dir=Path(tmp))
            self.assertIn("❌", result.stderr)

    def test_passthrough_without_scenario_prints_info(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/smoke.js"], scenario=None, tmp_dir=Path(tmp))
            self.assertIn("ℹ️", result.stderr)

    def test_scenario_without_explicit_script_prints_info_not_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper([], scenario="smoke", tmp_dir=Path(tmp))
            self.assertIn("ℹ️", result.stderr)
            self.assertNotIn("⚠️", result.stderr)

    def test_scenario_overriding_explicit_script_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_wrapper(["run", "/scripts/ignored.js"], scenario="gate", tmp_dir=Path(tmp))
            self.assertIn("⚠️", result.stderr)
            self.assertIn("/scripts/ignored.js", result.stderr)


class K6EntrypointTests(unittest.TestCase):
    def test_entrypoint_execs_wrapper_forwarding_all_args(self) -> None:
        contents = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("exec /scripts/k6-wrapper.sh \"$@\"", contents)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from pathlib import Path


class LegacyEntrypointTests(unittest.TestCase):
    def test_legacy_wrappers_route_through_punch_without_direct_compose_runs(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        for name in ("test-smoke", "test-gate", "test-journey", "test-suite"):
            contents = (repository / "bin" / name).read_text(encoding="utf-8")
            self.assertNotIn("docker compose run", contents)
            self.assertIn("bin/punch run", contents)

    def test_package_smoke_script_routes_through_punch_without_direct_compose_run(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        script = json.loads((repository / "package.json").read_text(encoding="utf-8"))["scripts"]["test:smoke"]
        self.assertNotIn("docker compose run", script)
        self.assertIn("bin/punch run", script)


if __name__ == "__main__":
    unittest.main()

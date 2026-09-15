from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPOSITORY = Path(__file__).resolve().parents[1]


class CiContractTests(unittest.TestCase):
    def test_ci_installs_the_runtime_and_tests_before_building_and_running_punch(self) -> None:
        workflow = yaml.safe_load((REPOSITORY / ".github/workflows/k6.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["test-suite"]["steps"]

        def index_of(predicate, description: str) -> int:
            for index, step in enumerate(steps):
                if predicate(step):
                    return index
            self.fail(f"missing CI step: {description}")

        setup = index_of(
            lambda step: step.get("uses") == "actions/setup-python@v5"
            and step.get("with", {}).get("python-version") == "3.11",
            "Python 3.11 setup",
        )
        install = index_of(
            lambda step: step.get("run") == "python -m pip install -r requirements.txt",
            "pinned requirements install",
        )
        unit_tests = index_of(
            lambda step: step.get("run") == "PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v",
            "direct Python unit suite",
        )
        build = index_of(lambda step: step.get("run") == "docker compose build", "Docker image build")
        run = index_of(lambda step: step.get("run") == "./bin/punch run all", "YAML-backed Punch run")

        self.assertLess(setup, install)
        self.assertLess(install, unit_tests)
        self.assertLess(unit_tests, build)
        self.assertLess(build, run)

    def test_active_guidance_declares_the_pinned_pyyaml_runtime(self) -> None:
        paths = [
            "src/punch/__main__.py",
            ".github/instructions/docker-compose.instructions.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/agents/punch-builder.agent.md",
            ".github/skills/punch-spec-driven-development/SKILL.md",
            ".github/skills/punch-documentation-and-adrs/SKILL.md",
            ".github/skills/punch-security-and-hardening/SKILL.md",
            ".github/prompts/punch-spec.prompt.md",
        ]

        for relative_path in paths:
            with self.subTest(path=relative_path):
                content = (REPOSITORY / relative_path).read_text(encoding="utf-8")
                self.assertIn("PyYAML", content)
                self.assertNotIn("stdlib-only orchestrator", content)
                self.assertNotIn("no pip dependencies", content)

    def test_consumer_repositories_own_workflows_while_punch_owns_the_engine(self) -> None:
        for relative_path in [
            ".github/instructions/punch-architecture.instructions.md",
            "docs/architecture/punch-boundaries.md",
            "docs/ai/operating-model.md",
        ]:
            with self.subTest(path=relative_path):
                content = (REPOSITORY / relative_path).read_text(encoding="utf-8")
                self.assertRegex(
                    content,
                    r"Consumer repositories own workflow YAML and k6 scripts\.\s+"
                    r"Punch owns the\s+reusable `src/punch/` engine",
                )


if __name__ == "__main__":
    unittest.main()

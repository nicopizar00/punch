from __future__ import annotations

import json
import shlex
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.workflow import load_workflow


class WorkflowCoverageTests(unittest.TestCase):
    def test_bundled_workflows_match_the_typescript_build_entries(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        build_command = json.loads((repository / "package.json").read_text(encoding="utf-8"))["scripts"]["build"]
        tokens = shlex.split(build_command)
        built_scripts = [
            f"/scripts/{Path(token).stem}.js"
            for token in tokens
            if token.startswith("src/tests/") and token.endswith(".ts")
        ]
        workflows = [load_workflow(path) for path in sorted((repository / "workflows" / "k6").glob("*.yaml"))]
        workflow_scripts = [workflow.k6_script for workflow in workflows]

        self.assertEqual(Counter(workflow_scripts), Counter(built_scripts))
        self.assertEqual(len([workflow.name for workflow in workflows]), len({workflow.name for workflow in workflows}))


if __name__ == "__main__":
    unittest.main()

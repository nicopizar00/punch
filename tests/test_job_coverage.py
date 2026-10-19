from __future__ import annotations

import json
import shlex
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.job import load_job


class JobCoverageTests(unittest.TestCase):
    def test_bundled_jobs_match_the_typescript_build_entries(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        build_command = json.loads((repository / "package.json").read_text(encoding="utf-8"))[
            "scripts"
        ]["build"]
        tokens = shlex.split(build_command)
        built_scripts = [
            f"/scripts/{Path(token).stem}.js"
            for token in tokens
            if token.startswith("src/tests/") and token.endswith(".ts")
        ]
        jobs = [load_job(path) for path in sorted((repository / "jobs" / "k6").glob("*.yaml"))]
        job_scripts = [job.k6_script for job in jobs]

        self.assertEqual(Counter(job_scripts), Counter(built_scripts))
        self.assertEqual(len([job.name for job in jobs]), len({job.name for job in jobs}))
        self.assertTrue(all(job.data is None for job in jobs))


if __name__ == "__main__":
    unittest.main()

# AGENTS.md

Punch (`bin/punch`, `src/punch/`) runs Punch jobs, k6 runs defined in
YAML, as one Docker Compose run each. Rules, layout, and commands live in
[CLAUDE.md](CLAUDE.md); usage in [README.md](README.md). The essentials:

- **Docker first.** Host needs Docker, Python 3.10+, and
  `python3 -m pip install -r requirements.txt`. Never assume host Node or k6.
- **Verify through Punch.** `./bin/punch doctor` and
  `./bin/punch run <job>` are the verification contract; a runtime change
  is done when `reports/state/punch-run.json` shows `"passed": true`.
  Unit tests: `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'`.
- **Humans merge.** Open the PR; humans approve and merge.
- **Don't edit `dist/` or `reports/`.** Both are generated.

## Adding a bundled k6 test

1. Add `src/tests/<name>.ts`; export `handleSummary` using `support/report.ts`.
2. Add it to the esbuild entry list in `package.json` `scripts.build`.
3. Add `jobs/k6/<name>.yaml` (`tests/test_job_coverage.py` checks
   the two lists match).
4. For a short `punch run <name>` alias, add it to `BUNDLED_JOBS` in
   `src/punch/__main__.py`.

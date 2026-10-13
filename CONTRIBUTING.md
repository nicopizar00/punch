# Contributing to k6-ts-docker

## Getting started

1. Install Docker and Python 3.10+. No Node or k6 on the host.
2. `python3 -m pip install -r requirements.txt`
3. `./bin/punch doctor`
4. Branch from `main` as `feature/*` or `fix/*`.

## Pull requests

- Target `main`. One feature or fix per PR.
- Use the issue and PR templates.
- Include how to verify. Runtime changes need `./bin/punch run smoke` (or the
  affected workflow) with `reports/state/punch-run.json` showing
  `"passed": true`; doc-only changes need diff review. See
  [validation.md](docs/workflows/validation.md).
- Update README / CHANGELOG when behavior or a public interface changes.

Usage and workflow YAML: [README.md](README.md). Rules: [CLAUDE.md](CLAUDE.md).

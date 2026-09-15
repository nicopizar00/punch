## Summary

Describe the change and why it is needed.

## Changes

- What changed
- Why

## How to verify

Per the [evidence matrix](../docs/workflows/validation.md):

- **Runtime-affecting change** — install the pinned PyYAML runtime with `python3 -m pip install -r requirements.txt`, then run `./bin/punch run smoke`. Files to check: reports/* and reports/state/punch-run.json (`passed: true` gates the change).
- **Documentation/Copilot-only change** — no runtime run expected; verify via diff review + a clean `punch-ai-governance` pass.

## Checklist

- [ ] Runtime-affecting: explicitly installed pinned PyYAML requirements, ran `./bin/punch run smoke` through Docker Compose (no host `npm`/`k6` required), artifacts produced in `reports/`, `reports/state/punch-run.json` shows `passed: true` — **or** Documentation/Copilot-only: no Build step needed, `punch-ai-governance` ran clean
- [ ] If `src/tests/` changed: dist bundles and CI steps updated
- [ ] If schema or DB changed: migration/seed steps included
- [ ] Docs updated (README / CHANGELOG.md) when behavior or public interfaces change
- [ ] Change is small and focused (one feature / fix per PR)

## Notes for reviewers

Add any context or areas you want reviewers to focus on.

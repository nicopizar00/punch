## Summary

What changed and why.

## How to verify

Per the [evidence matrix](../docs/jobs/validation.md):

- **Runtime-affecting** — install the pinned PyYAML runtime
  (`python3 -m pip install -r requirements.txt`), run `./bin/punch run smoke`
  (or the affected Punch job), and check `reports/state/punch-run.json` shows
  `"passed": true`.
- **Documentation-only** — diff review; no run expected.

## Checklist

- [ ] Evidence for the change class above (no host `npm`/`k6` on the evidence path)
- [ ] If `src/tests/` changed: `package.json` build entries and `jobs/k6/` updated
- [ ] If schema or DB changed: migration/seed steps included
- [ ] README / CHANGELOG updated when behavior or a public interface changes
- [ ] One feature or fix

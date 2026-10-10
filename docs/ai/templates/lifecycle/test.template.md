# Test — <topic>

> **Template.** Test report. **Evidence quoted
> verbatim;** prose `lite`.

- **Commands run** — <each with exit code>
  - `./bin/punch doctor` → exit <N>
  - `./bin/punch run <test>` → exit <N>
- **Artifacts produced** — <paths only>
  - `reports/state/punch-run.json`
  - <...>
- **Result** — pass | fail
- **If fail, classification** — implementation-related | environment-related | pre-existing
- **Minimal next action** — <one sentence: continue Review / back to Plan / escalate>

## Evidence (verbatim)
```json
<contents of reports/state/punch-run.json — must show "passed": true>
```

**Gate:** pass → Review. Fail (implementation) → Plan. Fail (environment/pre-existing) → human triage.

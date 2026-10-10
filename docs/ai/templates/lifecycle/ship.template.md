# Ship — <topic>

> **Template.** Ship-readiness summary. Caveman `full`.
> **Mechanical only**
> (git/gh) — Ship never merge, tag, or push `main`; human approve merge.

```
Summary: <one paragraph — what the change does and why>

Completed tasks:
  - <task ID — one-line goal>

Validation status:
  - reports/state/punch-run.json: passed: <bool>
  - Tests run: <list>

Known risks:
  - <one-liner or "none">

Operational impact:
  - artifacts changed: <none, or contract entries>
  - service contract changed: <none, or details>
  - host requirements changed: <none, or details>

Documentation status: <updated / not applicable>

Recommendation: ship | hold
  Reason: <one sentence>
```

**Gate:** human review PR, merge. CI re-run Test. Ship done when human merge.

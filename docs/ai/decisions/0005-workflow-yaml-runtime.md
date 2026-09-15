# ADR 0005: Workflow YAML Runtime

- Status: Accepted
- Date: 2026-09-15

## Decision

Normalized YAML is the sole execution definition for k6 workflows. PyYAML
6.0.3 is a declared host runtime dependency, installed explicitly with
`python3 -m pip install -r requirements.txt`; `punch run` never installs
dependencies.

Each selected workflow produces one explicit Docker Compose run. `punch run`
does not build images. A workflow may declare CSV output; harvesting tagged
`[CSV]` stdout records then requires confirmation and publication is atomic,
so a failed run cannot replace a prior CSV file.

## Consequences

The Python orchestrator remains standard-library based except for PyYAML.
`src/punch/execution.py` owns launch, separate stdout/stderr streaming,
confirmation, and CSV data harvesting. CI installs the pinned requirements,
runs the Punch unit suite, builds images, and then invokes the workflow-backed
CLI. Bundled workflows do not declare CSV output, so the default CI run needs
no output-data confirmation.

## Rejected alternatives

- JSON with a YAML extension, because the public execution format must be real,
  normalized YAML.
- Handwritten YAML parsing, because the declared PyYAML dependency gives the
  runtime a tested parser and clear failure behavior.
- Console-summary parsing, because only explicitly tagged stdout records are
  data; human-oriented console summaries are not an artifact contract.
- Native k6 CSV output, because the workflow-declared, path-configured
  publication contract is owned by Punch.

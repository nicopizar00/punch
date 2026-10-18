# ADR 0007: Punch jobs and Punch workflows

- Status: Accepted
- Date: 2026-10-18
- Implementation: built. The [reference architecture, section 10](../architecture/reference-architecture-and-implementation-guide.md#10-migrating-from-punch-workflows-to-punch-jobs) maps each old name to the new one.

## Context

"Workflow" named a Punch YAML unit, a GitHub workflow, an Argo Workflow, and
a consumer's ordered sequence of runs. On Kubernetes an Argo
`WorkflowTemplate` passed a `workflow` parameter that meant a Punch YAML
unit, and a GitHub workflow ran Punch "workflows" inside a GitHub job. A
reader had to infer the sense on almost every line.

## Decision

The YAML unit is a **Punch job**: one file, one k6 run,
`apiVersion: punch/v1`, `kind: K6Job`. The engine module is `punch.job`
(`K6Job`, `JobError`, `load_job`), the catalog is `JobCatalog`, execution is
`execute_job`, bundled jobs live in `jobs/k6/`, the run record names the YAML
in `"job"`, and `punch-k6-run` takes the parameter `job` (container variable
`PUNCH_JOB`).

An ordered sequence of Punch jobs linked by datasets is a **Punch workflow**.
A **step** is a job's position in a Punch workflow. Consumers own Punch
workflows; Punch runs one job at a time.

The layers then read in one direction: a GitHub workflow runs a GitHub job,
the GitHub job runs one Punch workflow, and the Punch workflow runs its Punch
jobs in order. On Kubernetes each Punch job is an Argo `WorkflowTemplate`.

The change is a clean break: no aliases and no second accepted kind. A file
with `kind: K6Workflow` fails with
`kind K6Workflow was renamed to K6Job; change the kind line`. `apiVersion`
stays `punch/v1`, because the kind check already rejects every old file with
a message that names the replacement.

## Consequences

- Every consumer changes its job files' `kind`, its imports, and its
  Kubernetes thin templates' parameter in one step, pinned to the Punch
  commit that lands the rename.
- "Job" also names a GitHub job. Documents qualify "GitHub job" wherever both
  appear.
- "Workflow" alone means a GitHub workflow or an Argo Workflow. A sequence of
  jobs is always written "Punch workflow".
- ADR 0005 and the dated specs keep the old term.

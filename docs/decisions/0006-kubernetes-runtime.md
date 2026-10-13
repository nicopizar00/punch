# ADR 0006: Kubernetes Runtime on Argo Workflows

- Status: Accepted
- Date: 2026-10-13

## Context

Punch runs one workflow as one `docker compose run`, driven by Python on the
host. A consumer needs the same runs inside Kubernetes — synced by Argo CD,
triggered by a CI job, with images in a registry and datasets in S3 — without
a host process or Python in the cluster.

## Decision

Punch ships a second, Kubernetes-native runtime in `k8s/`: a generic Argo
Workflows `WorkflowTemplate` (`punch-k6-run`), the POSIX shell step
`k6-run.sh` it runs inside the consumer's k6 image, and the executor RBAC.
Argo Workflows does scheduling and S3 artifact I/O. The shell step ports
Punch's core data contract: dataset preflight, stdout-only `[DATA]` harvest
with explicit opt-in, column checks, and atomic publication. Run evidence is
the Workflow status plus archived logs and reports.

The compose runtime is unchanged and remains the default. The menu, prompts,
catalog checks, sizing, and `--data` stay local-only.

## Consequences

- Anti-pattern 4 ("Python bypassing Compose") is a compose-runtime rule. On
  Kubernetes the step's pod is the isolation boundary and k6 runs directly in
  it.
- Two harvesters implement the `[DATA]` contract: Python (full CSV) for
  compose and shell (plain comma-separated payloads, no quoting) for
  Kubernetes. `tests/test_k8s_run_script.py` pins the shell side.
- Consumers own image mapping, the `punch-k6-options` presets ConfigMap, the
  artifact repository, and one thin WorkflowTemplate per workflow.

## Rejected alternatives

- Punch's Python engine in a runner image: keeps Python in the cluster and a
  host-shaped engine in a pod.
- k6-operator `TestRun`: its distributed load is not needed and it has no
  data handoff between runs.
- Plain Jobs with a harvest sidecar: hand-built artifact plumbing and a race
  with main-container exit.

# Punch on Kubernetes

A second, Kubernetes-native runtime for Punch workflows
([ADR 0006](../docs/decisions/0006-kubernetes-runtime.md)). Argo Workflows
runs the step and moves artifacts through S3; a POSIX shell step ports
Punch's core data contract. No Python and no Punch image run in the cluster.
The compose runtime (`punch run`) is unchanged.

| File | Holds |
| --- | --- |
| `punch-k6-run.yaml` | `WorkflowTemplate punch-k6-run`: templates `k6` and `k6-browser` |
| `k6-run.sh` | The step: validate → preflight → `k6 run` → `[DATA]` harvest |
| `rbac.yaml` | `punch-executor` ServiceAccount, Role (WorkflowTaskResults), RoleBinding |
| `kustomization.yaml` | Base: the above plus ConfigMap `punch-k6-run-script` |
| `kustomizeconfig.yaml` | `images:` fieldSpec for WorkflowTemplate containers |
| `examples/artifact-repositories.yaml` | Real-AWS S3 repository (IRSA) |

## Parameters

| Parameter | Required | Example | Meaning |
| --- | --- | --- | --- |
| `workflow` | yes | `http-orders` | Workflow id, used in messages |
| `script` | yes | `/scripts/scenarios/http-orders/http-orders.js` | k6 entry in the consumer image |
| `base-url` | yes | `http://bff.shop.svc.cluster.local:3001` | `BASE_URL` |
| `config` | no | `5-iterations` | Preset → `k6 run --config /punch/options/<config>.json` |
| `forward` | no | `EXPECT_TEMPERATURE USERS` | Names `extra-env` may set |
| `extra-env` | no | `EXPECT_TEMPERATURE=hot` | Space-separated `K=V`, each passed as `-e K=V`; values cannot contain spaces |
| `requires` | no | `carts` | Datasets that must exist with ≥ 1 row |
| `requires-hint` | no | `carts=browser-cart,http-cart` | Producers named in the preflight failure |
| `produces` | no | `orders=orderId` | Declared datasets and columns, `<ds>=<col>,<col>` space-separated |
| `produce` | no | `orders` or `all` | Datasets this run publishes (comma-separated) |

Container environment: `BASE_URL`, `K6_LOG_OUTPUT=stdout`, `K6_LOG_FORMAT=raw`,
and `DATA_<DATASET>_CSV=/scripts/data/<dataset>.csv` per required dataset
(`-` becomes `_`).

## Behavior

1. **Validate** — an undeclared `produce` entry or an `extra-env` name outside
   `forward` exits 2 before anything runs.
2. **Preflight** — a required dataset that is missing or has no row after its
   header exits 1: `<workflow> requires "<ds>"; produce it with: <producers>
   (--produce <ds>)`.
3. **Run** — k6's stdout is teed to `/punch/out/stdout.log`; stderr goes only
   to the pod log and is never harvested. A non-zero k6 exit ends the step
   with that code and publishes nothing.
4. **Harvest** — every `[DATA <ds>] payload` line on stdout is checked: an
   undeclared `<ds>` fails; for an opted-in `<ds>`, the comma-separated field
   count must match its columns; an opted-in dataset with zero rows fails.
   Only after the whole log passes are `<ds>.csv` files (header + rows)
   written to `/punch/out/datasets/` and `rows.txt` (`<ds>=<count>`) written.

Payloads are plain comma-separated values with no quoting; a quoted comma
fails the column check. The compose runtime's Python harvester accepts full
CSV.

## Artifacts and evidence

| Direction | Path | S3 key |
| --- | --- | --- |
| In (optional) | `/scripts/data` | `datasets/` |
| Out (optional, `archive: none`) | `/punch/out/datasets` | `datasets/` — only written files replace objects |
| Out (optional, `archive: none`) | `/scripts/reports` | `runs/{{workflow.name}}/reports/` |
| Logs | — | `archiveLogs: true` → repository `keyFormat` |

Evidence is the Workflow itself: its phase, node messages and exit codes, the
`datasets` output parameter (from `rows.txt`), and the archived logs and
reports. Runs end within `activeDeadlineSeconds: 900`.

Pods run as uid/gid 12345, the `grafana/k6` image user, so Argo's executor
writes input artifacts the step can read. A consumer image with a different
user needs a patch to `securityContext`.

## Consumer setup

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: perf
resources:
  - <path>/punch/k8s
  - artifact-repositories.yaml   # default S3 repository for this namespace
  - workflows/http-orders.yaml   # thin templates calling punch-k6-run
configurations:
  - <path>/punch/k8s/kustomizeconfig.yaml
images:
  - name: k6-scenarios
    newName: <registry>/<repo>/k6
    newTag: <tag>
  - name: k6-scenarios-browser
    newName: <registry>/<repo>/k6-browser
    newTag: <tag>
configMapGenerator:
  - name: punch-k6-options      # k6 options JSON presets, one key per file
    files: [options/5-iterations.json]
generatorOptions:
  disableNameSuffixHash: true
```

A thin template runs one workflow:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: http-orders
spec:
  entrypoint: main
  arguments:
    parameters:
      - { name: config, value: 5-iterations }
      - { name: produce, value: "" }
      - { name: base-url, value: "http://bff.shop.svc.cluster.local:3001" }
      - { name: extra-env, value: "" }
  templates:
    - name: main
      steps:
        - - name: k6
            templateRef: { name: punch-k6-run, template: k6 }
            arguments:
              parameters:
                - { name: workflow, value: http-orders }
                - { name: script, value: /scripts/scenarios/http-orders/http-orders.js }
                - { name: config, value: "{{workflow.parameters.config}}" }
                - { name: base-url, value: "{{workflow.parameters.base-url}}" }
                - { name: forward, value: "" }
                - { name: extra-env, value: "{{workflow.parameters.extra-env}}" }
                - { name: requires, value: carts }
                - { name: requires-hint, value: "carts=http-cart" }
                - { name: produces, value: "orders=orderId" }
                - { name: produce, value: "{{workflow.parameters.produce}}" }
```

Render with `kubectl kustomize --load-restrictor LoadRestrictionsNone` when
the base or presets live outside the kustomization root, and apply with
`--server-side` (Argo's CRDs exceed the client-side annotation limit).

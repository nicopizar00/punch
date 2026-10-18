# Punch on Kubernetes

A second, Kubernetes-native runtime for Punch workflows
([ADR 0006](../docs/decisions/0006-kubernetes-runtime.md)). Argo Workflows
runs the step and moves artifacts through S3; a POSIX shell step ports
Punch's core data contract. No Python and no Punch image run in the cluster.
The compose runtime (`punch run`) is unchanged. How each `punch run` step
maps here, the CI design (GitHub workflows, chains), dataset lifetime, design
gaps, weak points against k6 practice, and other clouds:
[orchestrator to Kubernetes](../docs/architecture/orchestrator-to-kubernetes.md).

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
| `requires` | no | `carts` | The one dataset that must exist with ≥ 1 row (more than one exits 2) |
| `requires-hint` | no | `carts=browser-cart,http-cart` | Producers named in the preflight failure |
| `produces` | no | `orders=orderId` | Declared datasets and columns, `<ds>=<col>,<col>` space-separated |
| `produce` | no | `orders` or `all` | Datasets this run publishes (comma-separated) |

Container environment: `BASE_URL`, `K6_LOG_OUTPUT=stdout`, `K6_LOG_FORMAT=raw`,
and `DATA_<DATASET>_CSV=/scripts/data/<dataset>.csv` per required dataset
(`-` becomes `_`).

## Behavior

1. **Validate** — an undeclared `produce` entry, an `extra-env` name outside
   `forward`, or more than one `requires` dataset exits 2 before anything runs.
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
| In (optional) | `/scripts/data/<requires>.csv` | `datasets/<requires>.csv` — one file, so an empty `datasets/` folder cannot break the load |
| Out (optional, `archive: none`) | `/punch/out/datasets` | `datasets/` — only written files replace objects |
| Out (optional, `archive: none`) | `/scripts/reports` | `runs/{{workflow.name}}/reports/` |
| Logs | — | `archiveLogs: true` → repository `keyFormat` |

Evidence is the Workflow itself: its phase, node messages and exit codes, the
`datasets` output parameter (from `rows.txt`), and the archived logs and
reports. Runs end within 900 s, including time spent Pending (`timeout: 900s`;
`activeDeadlineSeconds: 900` bounds the pod once it is on a node).

Pods run as uid/gid 12345, the `grafana/k6` image user, so Argo's executor
writes input artifacts the step can read. A consumer image with a different
user needs a patch to `securityContext`.

## Consumer image

The step runs inside the consumer's k6 image; Punch ships no image for this
runtime. The template replaces the image's entrypoint with
`sh /punch/bin/k6-run.sh`, so the image needs:

- `k6` on `PATH` and the busybox tools the step calls: `sh`, `awk`, `sed`,
  `tee`, `tr`. The `grafana/k6` images carry both.
- The compiled k6 entry at the absolute path the thin template passes as
  `script`.
- A `handleSummary` that writes its reports under `/scripts/reports/`, the
  only report directory the template uploads.
- One `[DATA <dataset>] payload` line on stdout per row a producer emits.
  `console.log` reaches stdout because the template sets
  `K6_LOG_OUTPUT=stdout`.

The `k6` template runs the image named `k6-scenarios` and `k6-browser` runs
`k6-scenarios-browser`; the consumer's kustomization maps both names to real
images. The browser template also mounts a 512 Mi memory-backed `/dev/shm`
for Chromium. Both templates pull with `imagePullPolicy: Always`, so every
tag must exist in a registry the nodes can reach: an image that exists only
in a node's local cache fails to start.

## Cluster prerequisites

Punch ships the step and its RBAC, not the platform around it. Before the
first apply, the cluster needs:

- Argo Workflows, with a controller that watches the run namespace. The
  minimal CRD set is enough.
- A default artifact repository in the run namespace: ConfigMap
  `artifact-repositories`, annotated
  `workflows.argoproj.io/default-artifact-repository: <key>`, whose `<key>`
  entry points at an S3-compatible bucket.
- A registry that holds the consumer's k6 images and that the nodes can pull
  from.

The kustomize base sets no namespace; the consumer's kustomization does. On
real AWS the repository has the shape of
[`examples/artifact-repositories.yaml`](examples/artifact-repositories.yaml).
A local S3-compatible server such as SeaweedFS takes the same shape with an
explicit endpoint, `insecure: true`, and a Secret for the keys:

```yaml
data:
  s3: |
    s3:
      endpoint: <service>.<namespace>.svc.cluster.local:<port>
      bucket: punch-artifacts
      insecure: true
      region: us-east-1
      keyFormat: "runs/{{workflow.name}}/{{pod.name}}"
      accessKeySecret: { name: s3-credentials, key: accessKey }
      secretKeySecret: { name: s3-credentials, key: secretKey }
```

The `k6` template was verified on minikube with Argo Workflows v4.1.5 and
SeaweedFS 4.48 as the S3 store, on 2026-10-13, including a producer to
consumer chain and a failed producer that left the previous dataset in place.
The `k6-browser` template has not run on a cluster yet. On that 4.5 GB
cluster, Argo's full-validation CRDs (about 12 MB of schemas) drove
kube-apiserver and Argo CD's application controller out of memory; the
minimal CRDs did not.

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

## From a workflow YAML to a thin template

The cluster never reads the workflow YAML. Each thin template restates one
workflow's facts, field by field:

| Workflow YAML | Thin template |
| --- | --- |
| `metadata.name` | `metadata.name` and the step's `workflow` |
| `spec.compose.service` | `templateRef.template`: `k6`, or `k6-browser` when the service runs the k6 browser image |
| `spec.k6.script` | `script`, the same absolute container path |
| `spec.k6.config` | Default of the `config` workflow parameter: the file name without `.json`. The file itself becomes a key of `punch-k6-options` |
| `spec.environment.forward` | `forward`: every name except `BASE_URL`, space-separated |
| `BASE_URL`, forwarded from the host | Default of the `base-url` workflow parameter: the target's in-cluster URL |
| `spec.data.requires` | `requires`: at most one dataset |
| The producers of each required dataset among the sibling workflow files | `requires-hint`: `<dataset>=<producer>,<producer>` |
| `spec.data.produces[].dataset` and `.columns` | `produces`: `<dataset>=<column>,<column>`, space-separated |

The workflow parameters `config`, `produce`, `base-url`, and `extra-env` pass
through to the step as `{{workflow.parameters.<name>}}`, so a run can
override them at submit time. `produce` and `extra-env` default to empty.

These workflow fields have no Kubernetes equivalent:

- `spec.environment.required`: the step does not check that a name is set.
- `spec.data.optional`: the template downloads only the `requires` dataset.
- `spec.data.directory` and `spec.data.mountedAt`: the dataset lands at
  `/scripts/data/<dataset>.csv`, and scripts read its path from
  `DATA_<DATASET>_CSV`.
- `spec.data.produces[].targets`, `spec.data.produces[].recommended`, and
  `spec.sizing`: sizing stays local
  ([ADR 0006](../docs/decisions/0006-kubernetes-runtime.md)).
- `spec.outputs.summary`: the reports are whatever `handleSummary` writes
  under `/scripts/reports/`.
- `spec.workingDirectory` and `spec.compose.file`: the kustomization's
  `images:` entries take their place.

Nothing keeps a thin template equal to its workflow YAML at run time. A
consumer pins the pair with a unit test that loads each workflow with
`punch.workflow.load_workflow`, derives `requires-hint` from
`punch.catalog.load_catalog(<workflows dir>).producers_of(<dataset>)`, and
compares the result with the thin template row by row.

## Run a workflow

Create a Workflow that references the thin template. Parameters left out
keep the template's defaults:

```bash
kubectl -n perf create -f - <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: http-cart-
spec:
  workflowTemplateRef:
    name: http-cart
  arguments:
    parameters:
      - { name: config, value: 1-iteration }
      - { name: produce, value: carts }
EOF
```

With the Argo CLI:
`argo -n perf submit --from workflowtemplate/http-cart -p config=1-iteration -p produce=carts --wait --log`.

| `punch run` | Workflow parameter |
| --- | --- |
| `--config PATH` | `config`: a preset name, the key `<name>.json` in `punch-k6-options`, never a path |
| `--produce DATASET` (repeatable, or `all`) | `produce`: comma-separated names, or `all` |
| Host variables named in `spec.environment.forward` | `extra-env`: space-separated `NAME=VALUE` pairs, values without spaces |
| `BASE_URL` from the host | `base-url` |
| `--data DATASET=PATH` | None: upload the file to `datasets/<dataset>.csv` in the bucket, where the next preflight reads it |
| `--size-for`, `--keep-going`, `--collect-logs`, `--no-input`, the `all` selector | None |

### Follow a run and read its evidence

A run is over when `.status.phase` is `Succeeded`, `Failed`, or `Error`; the
template's 900 s timeout bounds the wait. These queries read the evidence
listed under [Artifacts and evidence](#artifacts-and-evidence):

```bash
kubectl -n perf get workflow <run> -o jsonpath='{.status.phase}'
kubectl -n perf get workflow <run> -o jsonpath='{.status.message}'
kubectl -n perf get workflow <run> \
  -o jsonpath='{range .status.nodes.*}{.displayName}:{.phase}:exit={.outputs.exitCode} {end}'
kubectl -n perf get workflow <run> \
  -o jsonpath='{.status.nodes.*.outputs.parameters[?(@.name=="datasets")].value}'
kubectl -n perf logs -l workflows.argoproj.io/workflow=<run> -c main
```

The step's exit code is k6's own (99 when thresholds fail), 2 for invalid
inputs, or 1 for a failed preflight or harvest. The reports and the archived
log sit under `runs/<run>/` in the bucket. To stop a run:

```bash
kubectl -n perf patch workflow <run> --type merge -p '{"spec":{"shutdown":"Stop"}}'
```

### Chain workflows through datasets

Every run in a namespace reads and writes the same `datasets/` prefix of its
artifact repository. A producer run with `produce=<dataset>` replaces
`datasets/<dataset>.csv` only after k6 passed and every row passed its column
check, so a failed producer leaves the previous file in place. A consumer
run's preflight then reads that file. The order for the example template
above:

1. Run `http-cart` with `produce=carts`; its `datasets` output reads
   `carts=<rows>`.
2. Run `http-orders`. Its preflight finds rows in `datasets/carts.csv`, and
   k6 starts.

Datasets in the bucket and the compose runtime's local data files are
separate stores. The CI design chains inside one Workflow instead
([chain on Kubernetes](../docs/architecture/orchestrator-to-kubernetes.md#punch-workflow-on-kubernetes)).

## Sync with Argo CD

Argo CD can own the platform: one Application per kustomization. An
Application that installs Argo Workflows together with the thin templates
needs two sync options:

```yaml
syncPolicy:
  syncOptions:
    - ServerSideApply=true              # Argo Workflows' CRDs exceed the client-side annotation limit
    - SkipDryRunOnMissingResource=true  # templates sync in the same pass as the CRDs that define them
```

Argo CD syncs manifests only, so the images must be in the registry before
the sync. A Workflow annotated as a PostSync hook turns one run into a
deployment gate. Argo CD's built-in health check for `Workflow` resources
fails the sync when that run fails:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: postsync-smoke
  namespace: perf
  annotations:
    argocd.argoproj.io/hook: PostSync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
spec:
  workflowTemplateRef:
    name: http-cart
  arguments:
    parameters:
      - { name: config, value: 1-iteration }
```

`BeforeHookCreation` deletes the previous run only when the next sync starts,
so the last result stays inspectable. This setup was verified with Argo CD
v3.5.5 on minikube on 2026-10-13.

## Run on AWS

The manifests stay the same; three pieces change:

- Images move to ECR under an immutable tag, set with
  `kustomize edit set image k6-scenarios=<account>.dkr.ecr.<region>.amazonaws.com/<repo>/k6:<sha>`
  and the same for `k6-scenarios-browser`.
- The artifact repository points at a real bucket with `useSDKCreds: true`
  ([`examples/artifact-repositories.yaml`](examples/artifact-repositories.yaml)).
  The credentials come from the IAM role that IRSA binds to the
  `punch-executor` ServiceAccount.
- The Argo server runs with `--auth-mode=client` or SSO. `--auth-mode=server`
  (no login) suits a local cluster only.

No run on AWS has been recorded yet; this list follows the AWS shape of each
piece.

## Trigger from CI

Punch ships no CI job for this runtime. The design, with GitHub workflows
for one Punch workflow or a chain, OIDC instead of keys, and runs created only
after Argo CD has synced the commit, is in
[orchestrator to Kubernetes](../docs/architecture/orchestrator-to-kubernetes.md#github-actions).
CI never applies the kustomization.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `no matches for kind "WorkflowTemplate"` on apply | The CRDs and the templates applied in one pass, and the CRDs were still registering. Apply again. |
| Step pod in `ImagePullBackOff` | The tag is missing from the registry, or the nodes cannot reach it. The run fails at the 900 s timeout. |
| `requires "<dataset>"; produce it with: ...` | `datasets/<dataset>.csv` is missing or has no rows. Run a named producer with `produce=<dataset>`. |
| Exit 2 with `does not produce`, `does not forward`, or `is not NAME=VALUE` | A submit parameter breaks the thin template's declarations; the message names it. k6 did not start. |
| Exit 2 with `supports one required dataset per workflow` | The thin template lists more than one dataset in `requires`. |
| Exit 99 | k6 thresholds failed. Read the pod log. |
| `expected <n> columns, got "..."` | A `[DATA]` payload has the wrong field count. A quoted comma still counts as a separator here. |
| `no [DATA <dataset>] records were produced` | `produce` opted in a dataset the run emitted no rows for. Nothing was published. |
| `undeclared dataset "<dataset>"` | The script emitted a `[DATA]` line for a dataset missing from `produces`. |

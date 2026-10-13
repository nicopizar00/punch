"""k8s/: the punch-k6-run WorkflowTemplate, its RBAC, and kustomize wiring."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

K8S = Path(__file__).resolve().parents[1] / "k8s"
PARAMETERS = (
    "workflow",
    "script",
    "config",
    "base-url",
    "forward",
    "extra-env",
    "requires",
    "requires-hint",
    "produces",
    "produce",
)


def _documents(name: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all((K8S / name).read_text(encoding="utf-8")) if doc]


def _templates() -> dict[str, dict]:
    [template] = _documents("punch-k6-run.yaml")
    return {t["name"]: t for t in template["spec"]["templates"]}


def _env_name(parameter: str) -> str:
    return "BASE_URL" if parameter == "base-url" else "PUNCH_" + parameter.upper().replace("-", "_")


class PunchK6RunTemplateTests(unittest.TestCase):
    def test_declares_k6_and_k6_browser(self) -> None:
        self.assertEqual(set(_templates()), {"k6", "k6-browser"})

    def test_every_parameter_reaches_the_script_as_env(self) -> None:
        for name, template in _templates().items():
            with self.subTest(template=name):
                declared = [p["name"] for p in template["inputs"]["parameters"]]
                self.assertEqual(declared, list(PARAMETERS))
                env = {e["name"]: e["value"] for e in template["container"]["env"]}
                for parameter in PARAMETERS:
                    self.assertEqual(
                        env[_env_name(parameter)], "{{inputs.parameters.%s}}" % parameter
                    )
                self.assertEqual(env["K6_LOG_OUTPUT"], "stdout")
                self.assertEqual(env["K6_LOG_FORMAT"], "raw")

    def test_only_workflow_script_and_base_url_are_required(self) -> None:
        for template in _templates().values():
            required = {p["name"] for p in template["inputs"]["parameters"] if "default" not in p}
            self.assertEqual(required, {"workflow", "script", "base-url"})

    def test_runs_bounded_with_the_executor_account_and_archived_logs(self) -> None:
        # An unpullable image must fail the run instead of hanging a PostSync hook.
        for template in _templates().values():
            # timeout counts Pending time too (a pod that is never scheduled);
            # activeDeadlineSeconds only starts once the pod is on a node.
            self.assertEqual(template["timeout"], "900s")
            self.assertEqual(template["activeDeadlineSeconds"], 900)
            self.assertEqual(template["serviceAccountName"], "punch-executor")
            self.assertTrue(template["archiveLocation"]["archiveLogs"])

    def test_pod_runs_as_the_k6_image_user(self) -> None:
        # Argo's init container writes input artifacts as the pod user; as
        # root it creates /scripts/data 0700, which k6 (uid 12345) cannot read.
        for template in _templates().values():
            self.assertEqual(
                template["securityContext"],
                {"runAsUser": 12345, "runAsGroup": 12345, "runAsNonRoot": True},
            )

    def test_runs_the_mounted_script_in_the_consumer_image(self) -> None:
        images = {name: t["container"]["image"] for name, t in _templates().items()}
        self.assertEqual(images, {"k6": "k6-scenarios", "k6-browser": "k6-scenarios-browser"})
        for template in _templates().values():
            self.assertEqual(template["container"]["command"], ["sh", "/punch/bin/k6-run.sh"])
            mounts = {m["mountPath"]: m["name"] for m in template["container"]["volumeMounts"]}
            volumes = {v["name"]: v for v in template["volumes"]}
            self.assertEqual(volumes[mounts["/punch/bin"]]["configMap"]["name"], "punch-k6-run-script")
            self.assertEqual(volumes[mounts["/punch/options"]]["configMap"]["name"], "punch-k6-options")
            self.assertIn("emptyDir", volumes[mounts["/punch/out"]])
            self.assertIn("emptyDir", volumes[mounts["/scripts/reports"]])

    def test_browser_template_adds_shared_memory(self) -> None:
        browser = _templates()["k6-browser"]
        mounts = {m["mountPath"]: m["name"] for m in browser["container"]["volumeMounts"]}
        volumes = {v["name"]: v for v in browser["volumes"]}
        self.assertEqual(volumes[mounts["/dev/shm"]]["emptyDir"], {"medium": "Memory", "sizeLimit": "512Mi"})
        k6_mounts = {m["mountPath"] for m in _templates()["k6"]["container"]["volumeMounts"]}
        self.assertNotIn("/dev/shm", k6_mounts)

    def test_datasets_move_through_one_s3_prefix(self) -> None:
        for template in _templates().values():
            # One optional file, not the whole prefix: an empty datasets/
            # folder makes Argo fail an optional directory input.
            [dataset_input] = template["inputs"]["artifacts"]
            self.assertEqual(
                (dataset_input["path"], dataset_input["s3"]["key"], dataset_input["optional"]),
                (
                    "/scripts/data/{{inputs.parameters.requires}}.csv",
                    "datasets/{{inputs.parameters.requires}}.csv",
                    True,
                ),
            )
            outputs = {a["name"]: a for a in template["outputs"]["artifacts"]}
            self.assertEqual(outputs["datasets"]["path"], "/punch/out/datasets")
            self.assertEqual(outputs["datasets"]["s3"]["key"], "datasets")
            self.assertEqual(outputs["reports"]["path"], "/scripts/reports")
            self.assertEqual(outputs["reports"]["s3"]["key"], "runs/{{workflow.name}}/reports")
            for artifact in outputs.values():
                self.assertTrue(artifact["optional"])
                self.assertEqual(artifact["archive"], {"none": {}})
            [rows] = template["outputs"]["parameters"]
            self.assertEqual(rows["name"], "datasets")
            self.assertEqual(rows["valueFrom"], {"path": "/punch/out/rows.txt", "default": ""})


class KustomizeWiringTests(unittest.TestCase):
    def test_kustomization_generates_the_script_configmap_with_a_stable_name(self) -> None:
        [kustomization] = _documents("kustomization.yaml")
        self.assertEqual(kustomization["resources"], ["rbac.yaml", "punch-k6-run.yaml"])
        [generator] = kustomization["configMapGenerator"]
        self.assertEqual((generator["name"], generator["files"]), ("punch-k6-run-script", ["k6-run.sh"]))
        self.assertTrue(kustomization["generatorOptions"]["disableNameSuffixHash"])

    def test_images_fieldspec_reaches_workflow_template_containers(self) -> None:
        [config] = _documents("kustomizeconfig.yaml")
        self.assertEqual(
            config["images"], [{"path": "spec/templates[]/container/image", "kind": "WorkflowTemplate"}]
        )

    def test_executor_role_allows_task_results_only(self) -> None:
        role = next(d for d in _documents("rbac.yaml") if d["kind"] == "Role")
        self.assertEqual(
            role["rules"],
            [{"apiGroups": ["argoproj.io"], "resources": ["workflowtaskresults"], "verbs": ["create", "patch"]}],
        )


if __name__ == "__main__":
    unittest.main()

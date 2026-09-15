from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class CsvOutput:
    path: Path


@dataclass(frozen=True)
class K6Workflow:
    source_path: Path
    name: str
    working_directory: Path
    compose_file: Path
    compose_service: str
    k6_script: str
    forward_environment: tuple[str, ...]
    required_environment: tuple[str, ...]
    csv_output: CsvOutput | None


ROOT_KEYS = {"apiVersion", "kind", "metadata", "spec"}
METADATA_KEYS = {"name"}
SPEC_KEYS = {"workingDirectory", "compose", "k6", "environment", "outputs"}
COMPOSE_KEYS = {"file", "service"}
K6_KEYS = {"script"}
ENVIRONMENT_KEYS = {"forward", "required"}
OUTPUT_KEYS = {"csv"}
CSV_KEYS = {"path"}
NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ENV_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _resolve_beneath(base: Path, raw: str, field: str) -> Path:
    candidate = (base / raw).resolve()
    if candidate != base and base not in candidate.parents:
        raise WorkflowError(f"{field} escapes spec.workingDirectory")
    return candidate


def _load_yaml(text: str) -> Any:
    try:
        import yaml
    except ModuleNotFoundError as error:
        raise WorkflowError(
            "PyYAML is required; install it with python3 -m pip install -r requirements.txt"
        ) from error

    try:
        for event in yaml.parse(text):
            if getattr(event, "anchor", None) is not None:
                raise WorkflowError("YAML aliases and anchors are not supported")

        class StrictSafeLoader(yaml.SafeLoader):
            pass

        def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
            mapping: dict[Any, Any] = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if not isinstance(key, str):
                    raise WorkflowError("YAML mapping keys must be strings")
                if key in mapping:
                    raise WorkflowError(f"duplicate YAML key: {key}")
                mapping[key] = loader.construct_object(value_node, deep=deep)
            return mapping

        StrictSafeLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
        )
        return yaml.load(text, Loader=StrictSafeLoader)
    except WorkflowError:
        raise
    except yaml.YAMLError as error:
        raise WorkflowError(str(error)) from error


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{field} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise WorkflowError(f"{field} keys must be strings")
    return value


def _allowed_keys(value: Any, allowed: set[str], field: str) -> dict[str, Any]:
    mapping = _mapping(value, field)
    for key in mapping:
        if key not in allowed:
            raise WorkflowError(f"unknown field {field}.{key}")
    return mapping


def _required(mapping: dict[str, Any], key: str, field: str) -> Any:
    if key not in mapping:
        raise WorkflowError(f"{field}.{key} is required")
    return mapping[key]


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"{field} must be a non-empty string")
    return value


def _environment_names(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkflowError(f"{field} must be a list")
    names: list[str] = []
    for name in value:
        if not isinstance(name, str) or not ENV_PATTERN.fullmatch(name):
            raise WorkflowError(f"{field} entries must match {ENV_PATTERN.pattern}")
        if name in names:
            raise WorkflowError(f"{field} must not contain duplicate names")
        names.append(name)
    return tuple(names)


def load_workflow(path: Path) -> K6Workflow:
    source_path = Path(path).resolve()
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowError(f"could not read workflow file: {source_path}") from error

    root = _allowed_keys(_load_yaml(text), ROOT_KEYS, "root")
    if _required(root, "apiVersion", "root") != "punch/v1":
        raise WorkflowError("apiVersion must be punch/v1")
    if _required(root, "kind", "root") != "K6Workflow":
        raise WorkflowError("kind must be K6Workflow")

    metadata = _allowed_keys(_required(root, "metadata", "root"), METADATA_KEYS, "metadata")
    name = _string(_required(metadata, "name", "metadata"), "metadata.name")
    if not NAME_PATTERN.fullmatch(name):
        raise WorkflowError(f"metadata.name must match {NAME_PATTERN.pattern}")

    spec = _allowed_keys(_required(root, "spec", "root"), SPEC_KEYS, "spec")
    working_directory_raw = _string(
        _required(spec, "workingDirectory", "spec"), "spec.workingDirectory"
    )
    working_directory = (source_path.parent / working_directory_raw).resolve()
    if source_path != working_directory and working_directory not in source_path.parents:
        raise WorkflowError("workflow file must be beneath spec.workingDirectory")

    compose = _allowed_keys(_required(spec, "compose", "spec"), COMPOSE_KEYS, "spec.compose")
    compose_file = _resolve_beneath(
        working_directory,
        _string(_required(compose, "file", "spec.compose"), "spec.compose.file"),
        "compose file",
    )
    if not compose_file.is_file():
        raise WorkflowError("compose file does not exist")
    compose_service = _string(
        _required(compose, "service", "spec.compose"), "spec.compose.service"
    )

    k6 = _allowed_keys(_required(spec, "k6", "spec"), K6_KEYS, "spec.k6")
    k6_script = _string(_required(k6, "script", "spec.k6"), "spec.k6.script")
    if not k6_script.startswith("/"):
        raise WorkflowError("spec.k6.script must be an absolute container path")

    environment = _allowed_keys(spec.get("environment", {}), ENVIRONMENT_KEYS, "environment")
    forward_environment = _environment_names(environment.get("forward", []), "environment.forward")
    required_environment = _environment_names(environment.get("required", []), "environment.required")
    if not set(required_environment).issubset(forward_environment):
        raise WorkflowError("environment.required must also appear in environment.forward")

    outputs = _allowed_keys(spec.get("outputs", {}), OUTPUT_KEYS, "outputs")
    csv_output = None
    if "csv" in outputs:
        csv = _allowed_keys(outputs["csv"], CSV_KEYS, "outputs.csv")
        csv_output = CsvOutput(
            _resolve_beneath(
                working_directory,
                _string(_required(csv, "path", "outputs.csv"), "outputs.csv.path"),
                "csv path",
            )
        )

    return K6Workflow(
        source_path=source_path,
        name=name,
        working_directory=working_directory,
        compose_file=compose_file,
        compose_service=compose_service,
        k6_script=k6_script,
        forward_environment=forward_environment,
        required_environment=required_environment,
        csv_output=csv_output,
    )

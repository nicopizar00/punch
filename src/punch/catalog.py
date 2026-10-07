"""Cross-workflow data links: who produces and who requires each dataset.

A catalog is every workflow YAML in one directory. Producers name their
target workflows; consumers only name the datasets they require. Loading the
catalog checks both sides agree, so a run fails before Docker when a link is
broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from punch.workflow import K6Workflow, WorkflowError, load_workflow


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowCatalog:
    workflows: Mapping[str, K6Workflow]

    def producers_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(sorted(
            name for name, workflow in self.workflows.items()
            if workflow.data is not None and workflow.data.product(dataset) is not None
        ))

    def consumers_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(sorted(
            name for name, workflow in self.workflows.items()
            if workflow.data is not None
            and (dataset in workflow.data.requires or dataset in workflow.data.optional)
        ))

    def recommended_producer(self, dataset: str) -> str | None:
        """First producer, by name, that flags `dataset` as recommended."""
        for name in self.producers_of(dataset):
            if self.workflows[name].data.product(dataset).recommended:
                return name
        return None


def load_catalog(directory: Path) -> WorkflowCatalog:
    workflows: dict[str, K6Workflow] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            workflow = load_workflow(path)
        except WorkflowError as error:
            raise CatalogError(f"{path.name}: {error}") from error
        if workflow.name in workflows:
            raise CatalogError(f'duplicate workflow name "{workflow.name}"')
        workflows[workflow.name] = workflow
    catalog = WorkflowCatalog(workflows)
    _validate(catalog)
    return catalog


def _validate(catalog: WorkflowCatalog) -> None:
    columns_by_dataset: dict[str, tuple[str, ...]] = {}
    for name, workflow in catalog.workflows.items():
        if workflow.data is None:
            continue
        for product in workflow.data.produces:
            known = columns_by_dataset.setdefault(product.dataset, product.columns)
            if known != product.columns:
                raise CatalogError(f'producers of "{product.dataset}" declare different columns')
            for target in product.targets:
                target_workflow = catalog.workflows.get(target)
                if target_workflow is None:
                    raise CatalogError(f'{name} targets unknown workflow "{target}"')
                if target_workflow.data is None or (
                    product.dataset not in target_workflow.data.requires
                    and product.dataset not in target_workflow.data.optional
                ):
                    raise CatalogError(
                        f'{target} does not require "{product.dataset}" (targeted by {name})'
                    )
    # Second pass: a broken target link above is the more specific error, so
    # orphaned requirements are only reported once every link checks out.
    for name, workflow in catalog.workflows.items():
        if workflow.data is None:
            continue
        for dataset in workflow.data.requires:
            if not catalog.producers_of(dataset):
                raise CatalogError(f'{name} requires "{dataset}" but no workflow produces it')

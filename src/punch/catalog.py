"""Cross-job data links: who produces and who requires each dataset.

A catalog is every job YAML in one directory. Producers name their
target jobs; consumers only name the datasets they require. Loading the
catalog checks both sides agree, so a run fails before Docker when a link is
broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from punch.job import JobError, K6Job, load_job


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class JobCatalog:
    jobs: Mapping[str, K6Job]

    def producers_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name, job in self.jobs.items()
                if job.data is not None and job.data.product(dataset) is not None
            )
        )

    def consumers_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name, job in self.jobs.items()
                if job.data is not None
                and (dataset in job.data.requires or dataset in job.data.optional)
            )
        )

    def recommended_producer(self, dataset: str) -> str | None:
        """First producer, by name, that flags `dataset` as recommended."""
        for name in self.producers_of(dataset):
            data = self.jobs[name].data
            product = data.product(dataset) if data is not None else None
            if product is not None and product.recommended:
                return name
        return None


def load_catalog(directory: Path) -> JobCatalog:
    jobs: dict[str, K6Job] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            job = load_job(path)
        except JobError as error:
            raise CatalogError(f"{path.name}: {error}") from error
        if job.name in jobs:
            raise CatalogError(f'duplicate job name "{job.name}"')
        jobs[job.name] = job
    catalog = JobCatalog(jobs)
    _validate(catalog)
    return catalog


def _validate(catalog: JobCatalog) -> None:
    columns_by_dataset: dict[str, tuple[str, ...]] = {}
    for name, job in catalog.jobs.items():
        if job.data is None:
            continue
        for product in job.data.produces:
            known = columns_by_dataset.setdefault(product.dataset, product.columns)
            if known != product.columns:
                raise CatalogError(f'producers of "{product.dataset}" declare different columns')
            for target in product.targets:
                target_job = catalog.jobs.get(target)
                if target_job is None:
                    raise CatalogError(f'{name} targets unknown job "{target}"')
                if target_job.data is None or (
                    product.dataset not in target_job.data.requires
                    and product.dataset not in target_job.data.optional
                ):
                    raise CatalogError(
                        f'{target} does not require "{product.dataset}" (targeted by {name})'
                    )
    # Second pass: a broken target link above is the more specific error, so
    # orphaned requirements are only reported once every link checks out.
    for name, job in catalog.jobs.items():
        if job.data is None:
            continue
        for dataset in job.data.requires:
            if not catalog.producers_of(dataset):
                raise CatalogError(f'{name} requires "{dataset}" but no job produces it')

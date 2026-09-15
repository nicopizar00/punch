# Contributing to k6-ts-docker

Thanks for wanting to contribute. Follow these lightweight rules to keep changes reviewable.

## Getting started

1. Install Docker and Python 3 (>=3.10). No Node or k6 is required on the host.
2. Install the pinned Punch runtime with `python3 -m pip install -r requirements.txt`.
3. Run `./bin/punch doctor` to confirm your environment.
4. Use a feature branch named `ai-ready/*` or `feature/*`.

## Branch and PR rules

- Target the default branch (`main`) via a pull request from a feature branch.
- Keep PRs small and focused. Each PR should include a verification step in the description.
- Use the provided issue and PR templates.

## Running and testing locally

```bash
python3 -m pip install -r requirements.txt
docker compose build
./bin/punch run smoke
./bin/punch run path/to/workflow.yaml
./bin/punch run path/to/csv-workflow.yaml --confirm-output-data
```

`punch run` performs one Compose run for each selected YAML workflow and never
builds images. CSV is optional, declared with a destination by the workflow,
and published by `src/punch/execution.py` only after success. Its schema is the
ordered tag-stripped `[CSV]` stdout records; a pre-existing CSV does not prove
the current run passed. CI must pass `--confirm-output-data` when selecting a
CSV workflow; bundled workflows do not declare CSV output.

## What to include in PRs

- Short description and how to validate locally
- Which reports or artifacts changed
- Reference any docs or AGENTS.md updates

## Contact

For questions about the AI lifecycle or prompts, see [`docs/ai/operating-model.md`](docs/ai/operating-model.md) and the prompts under [`.github/prompts/`](.github/prompts).

# Spec: Punch workflows in the menu

> **Status:** Implemented 2026-10-19. Supersedes in part
> [`spec-target-data-sizing.md`](spec-target-data-sizing.md): its "Chained
> sizing" non-goal and its "Sizing never chains a second run" principle no
> longer bind the menu's `Run workflow` option. They still bind `Run job`
> and `punch run`.

## Goal

`punch menu` gains a `Run workflow` option that runs a Punch workflow: an
ordered list of Punch jobs in which each step reads the datasets that earlier
steps write. The operator picks the jobs one step at a time, picks the last
step's load, and turns data sizing on or off. With data sizing on, Punch sizes
every earlier step for the step after it, resolving from the last step back,
so step 1 writes enough rows for a step 2 that is itself sized for step 3.

Example, with the parent repository's catalog and `5-iterations` on the last
step of `http-cart` → `http-orders` → `http-orders-status`:

| Step | Job | Reader needs | Runs |
| --- | --- | --- | --- |
| 3 | `http-orders-status` | none (last step) | `5-iterations`: 5 iterations, 1 VU |
| 2 | `http-orders` | 5 `orders` rows | ceil(5 × 1.15) = 6 iterations, 1 VU |
| 1 | `http-cart` | 6 `carts` rows | ceil(6 × 1.15) = 7 iterations, 1 VU |

The margin (0.15) and paces come from the jobs' `spec.sizing` in the parent
repository (`tests/performance/k6/jobs/*.yaml`). Its `./dev perf:workflow
http-cart http-orders http-orders-status:5-iterations --plan` prints the same
7, 6, and 5 iterations.

Today the menu and `punch run --size-for` size one producer for one target.
To size step 1 for a sized step 2, the operator needs step 2's sized config as
step 1's target config. Punch writes that config only when step 2 runs, and
step 2 cannot run before step 1 has written its data. The parent repository's
`./dev perf:workflow` plans the whole workflow first, but its planner lives in
the parent repository (`scripts/pg/perf_workflow.py`), outside Punch.

## Design principles

- Job specifics live only in job YAML. Step links come from `spec.data`
  (`produces[].targets`, `requires`, `optional`); pace, budget, and margin
  come from `spec.sizing`. No job or dataset name appears in Python.
- One standard-library-only module, `punch.workflow`, owns step linking and
  chained sizing, and calls `punch.sizing` for every producer and reader pair.
  Its planning functions are pure; `run_workflow` is its only function that
  starts Docker or writes files. The menu is its first caller. The parent repository's `perf:workflow`
  becomes its second caller in a separate change.
- Each step is one `docker compose run --rm` through
  `execution.execute_job`, the path `Run job` uses. A workflow adds step
  order, data wiring, and evidence; it adds no execution semantics.
- A workflow never reads a dataset file left by an earlier run. Each read
  comes from a step of this workflow, and an optional dataset that no earlier
  step writes uses the scenario's built-in data.
- The load rules match the parent repository's `perf-workflow` GitHub form.
  The picked load goes on the last step. With data sizing on, every earlier
  step is `AUTO`; with data sizing off, every step gets the picked load.
- `Run job`, `punch run`, and `--size-for` keep their current behavior.

## Non-goals

- A `punch workflow` CLI subcommand. The `punch.workflow` API leaves room for
  one.
- A load per step in the menu. The parent repository's
  `./dev perf:workflow <job>:<shape>` keeps per-step control.
- A custom `BASE_URL` per step. `Run job` keeps the custom URL prompt.
- The Kubernetes runtime (`k8s/`).
- Rules the parent repository owns: preset names and their `-browser`
  variants, the 4-VU browser cap and its peak-VU check, empty `none` steps,
  custom-config path rules, `spec.json`, and the GitHub job summary.
- A job that appears twice in one workflow, and cycles.
- Producer or consumer fixtures in `jobs/k6/`. The bundled jobs declare no
  datasets.
- The parent repository's `perf:workflow` delegating its planning to
  `punch.workflow`. That change gets its own spec in the parent repository.
- Parallel steps.

## Definitions

- *Punch workflow*: an ordered list of Punch jobs, the *steps*, numbered from
  1. The menu builds workflows of two or more steps; `plan_workflow` accepts
  one or more.
- *Writer* of dataset `D` for step `n`: the latest step before `n` whose job
  produces `D`.
- *Edge* from job `P` to job `J`: `P` produces a dataset `D` whose `targets`
  include `J`, and `J` declares `D` in `requires` or `optional`.
- *First-step candidates* `first_jobs(catalog)`: every job whose `requires`
  is empty and for which `next_jobs([job], catalog)` is not empty, in catalog
  order (job file names sorted).
- *Next-step candidates* `next_jobs(chosen, catalog)`: every job `J` not in
  `chosen` that has an edge from the last job of `chosen`, and whose every
  `requires` dataset has a writer among `chosen`. Catalog order. Each
  candidate carries the datasets it reads from the last chosen job.
- *Load*: one of
  - `AUTO`: sized for the step's sizing reader when Punch can size the pair;
    otherwise the job's own config;
  - `None`: the job's own `spec.k6.config`, or the script's options when the
    job has none, never sized;
  - a `Path`: that k6 options JSON, never sized.
- *Sizing reader* of step `s`: the first later step that reads a dataset `s`
  writes and that the dataset's `targets` name.
- *Resolved config* of each step, computed from the last step back:
  - a `Path` load: that file's options (`read_k6_config`);
  - a `None` load: the job's `spec.k6.config` options, or `{}`;
  - an `AUTO` load with a sizing reader: the config of
    `size_producer(step job, reader job name, catalog, reader's resolved
    config)`. Because the reader resolves first, a sized reader passes its
    own sized config;
  - an `AUTO` load without a sizing reader, or whose `size_producer` raises
    `SizingError`: the job's own config, plus a warning in the second case.
- *Rows needed* of dataset `D` written by step `s`: `rows_needed(first
  reader's job, first reader's resolved config)`, when estimable.

## Functional requirements

1. **`punch.workflow`** (new, standard library only):

   | Name | Contract |
   | --- | --- |
   | `AUTO` | The sized-load marker |
   | `WorkflowStep` | Dataclass: `number`, `job`, `load`, `config` (resolved options), `config_path` (the file for `--config`; `None` for script options or before a sized config is written), `sizing` (`SizingPlan` or `None`), `sized_for` (reader step number or `None`), `produce`, `reads` (dataset → writer step number), `built_in` (optional datasets left on built-in data), `readers` (dataset → reader step numbers), `rows_needed` (dataset → rows) |
   | `WorkflowPlan` | Dataclass: `steps`, `errors`, `warnings` |
   | `first_jobs(catalog)` | First-step candidates, as defined |
   | `next_jobs(chosen, catalog)` | Next-step candidates as `(job name, datasets read)` pairs |
   | `plan_workflow(jobs, loads, catalog)` | Validates, links, and resolves; never raises for a bad request, records it in `errors` |
   | `step_label(step)` | The plan's load text: `auto: sized for step 2 (7 iterations, 1 VU)`, the options file stem, `job default (<stem>)`, or `script options` |
   | `data_label(step)` | `reads <D> from step <n>`, `<D>: built-in data`, and `writes <D>`, joined with `; `, or `none` |
   | `run_workflow(plan, catalog, *, environment, base_urls, state_dir)` | Runs the plan (requirement 3); returns the exit code |

   `plan_workflow` records these errors, and stops at the first stage with
   errors (selection, then linking):
   - `jobs` and `loads` differ in length, or `jobs` is empty;
   - `step <n>: unknown job "<name>"`;
   - `step <n> <job> repeats step <m>`;
   - `step <n> <job>: <JobError text>` for an unreadable `Path` load;
   - `step <n> <job>: unknown load <value>` for a load that is not `AUTO`,
     `None`, or a `Path`;
   - `step <n> <job> reads <D>; no earlier step writes it. Put <producers>
     before step <n>`;
   - `step <n> <job> reads <D> from step <s>, but <path> is outside its data
     directory <dir>`.

   It records these warnings:
   - `steps <a> and <b> read <D> from step <s>; its rows are reused`;
   - `step <n> <job>: not sized for step <r> (<SizingError text>); it runs its
     default config`.

   A step's `produce` holds only the datasets a later step reads, in
   `produces` order.

2. **Data wiring.** For each step, `run_workflow` passes `execute_job`:
   - `produce`: the step's `produce`;
   - `data_overrides`: every dataset in `reads` mapped to its writer's
     `job.data.host_path(D)`. `execution.data_environment` turns an override
     into a container path relative to the reader's `data.directory`, so
     `plan_workflow` rejects a writer whose file lies outside it;
   - `optional_choices`: `True` for each optional dataset in `reads`, `False`
     for each dataset in `built_in`;
   - `config`: the sized config file `write_producer_config(sizing,
     state_dir)` writes for a sized step, otherwise `config_path`;
   - `producers_of=catalog.producers_of` and `docker_run_confirmed=True`;
   - the environment plus `BASE_URL` from `base_urls[job.compose_service]`
     when the job forwards `BASE_URL`.

   `run_workflow` writes every sized config before step 1 runs.

3. **Run.** `run_workflow` runs the steps in order:
   1. After a passing step, `run_workflow` prints its metrics with the lines
      `Run job` prints. The menu's `_print_metrics` moves to
      `punch.execution` as `print_metrics`, and both callers use it.
      For each dataset the step wrote with fewer rows than its rows needed,
      it warns `step <s> <job> wrote <r> <D> rows; step <n> reads <needed>, so
      rows repeat` and continues.
   2. The first failing step stops the workflow; every later step becomes
      `skipped`. The exit code is the failed step's k6 exit code, or 1 when
      k6 never ran.
   3. `KeyboardInterrupt` during a step marks it `interrupted` and every
      later step `skipped`, writes the evidence, and re-raises, so the menu
      exits 130 as it does today.
   4. After the last step that ran, when any step started Docker,
      `confirm_delete_consumed` offers to delete the dataset files this
      workflow wrote, once. It asks only on a terminal; the default is no.
   5. All steps passing → exit code 0.

4. **Evidence.** `run_workflow` writes `<state_dir>/punch-workflow.json`
   before step 1, after every step, and on interruption, then prints
   `[punch] evidence written: <path>`. `punch-run.json` is not touched.

   | Field | Value |
   | --- | --- |
   | `version` | `1` |
   | `outcome` | `running`, `passed`, `failed`, or `interrupted` |
   | `passed` | `true` only when `outcome` is `passed` |
   | `jobs` | Job names in step order |
   | `steps[].step`, `steps[].job` | Step number and job name |
   | `steps[].load` | `auto`, `job default`, or the options file path |
   | `steps[].label` | `step_label(step)` |
   | `steps[].config` | The path passed as `--config`, or `null` for script options |
   | `steps[].sizing` | `sizing_evidence(plan, produced)` for a sized step, else `null` |
   | `steps[].produce`, `steps[].reads`, `steps[].builtIn` | The step's data wiring |
   | `steps[].status` | `pending`, `passed`, `failed`, `skipped`, or `interrupted` |
   | `steps[].exitCode`, `steps[].failure` | From the step's `ExecutionResult`, or `null` |
   | `steps[].rows` | Dataset → rows written (`DatasetResult.record_count`) |
   | `warnings` | Plan warnings, then run warnings, in order |

   Paths are strings relative to the current working directory when they lie
   beneath it, absolute otherwise.

5. **Menu.**
   1. The top-level picker lists `Run job`, `Run workflow`, and
      `Monitoring setup`. `Run job` and `Monitoring setup` keep their flows.
   2. `Step 1:` shows the jobs table `Run job` shows (`_job_menu_rows`),
      restricted to `first_jobs(catalog)`. With no candidates the menu prints
      `[punch] no job in <jobs dir> writes data another job reads` and
      returns 1.
   3. `Step <n>, after <previous job>:` lists `next_jobs` as
      `<job>  reads <D>[, <D2>]`. From step 3 on, `Done (<n - 1> steps)` is
      the last entry. The cursor starts on the first candidate.
   4. When step 3 or later has no candidate, the menu prints
      `[punch] no job reads what <previous job> writes; the workflow has
      <n - 1> steps` and moves on.
   5. `Load options for <last job> (last step):` is `Run job`'s options
      picker (`Job default (<stem>)` or `Job default (script options)`, then
      every `*.json` in the options directory). `Job default` gives the
      load `None`.
   6. `Data sizing (each earlier step is sized for the next)? (Y/n)`,
      default yes. An answer starting with `n` turns it off. On: every
      earlier load is `AUTO` and the last load is the picked one. Off: every
      load is the picked one.
   7. `BASE_URL` per compose service is `os.environ["BASE_URL"]` when set,
      else that service's default file (`_default_base_url`), without a
      prompt.
   8. The plan prints before any Docker call:

      ```text
      Punch workflow plan
        step 1  http-cart           auto: sized for step 2 (7 iterations, 1 VU)  (writes carts)
        step 2  http-orders         auto: sized for step 3 (6 iterations, 1 VU)  (reads carts from step 1; writes orders)
        step 3  http-orders-status  5-iterations  (reads orders from step 2)
        BASE_URL  k6: <url>
      ```

      Then each warning as `[punch] warning: <text>`. Plan errors print as
      `[punch] <text>` on stderr, and the menu returns 1.
   9. `Run these <n> steps? (y/N)`, default no. A no prints
      `[punch] workflow not run.` and returns 0 with no Docker call.
   10. No data picker runs; requirement 2 settles every data source.
   11. Esc on any picker cancels the menu as on every other screen:
       `[punch] menu canceled.`, exit 0, no Docker call.

6. **Docs.**
   - `README.md`: the menu row and section describe `Run workflow`; the
     `Specs` row lists this spec.
   - `docs/jobs/validation.md`: a `punch-workflow.json` section beside
     `punch-run.json`.
   - `CLAUDE.md`: the `src/punch/` layout row lists `workflow` (pure
     planning; `run_workflow` runs the steps).
   - `CHANGELOG.md`: an `Added` entry under `[Unreleased]`.
   - `spec-target-data-sizing.md`: the status line names this spec as
     superseding its "Chained sizing" non-goal for the menu.

## Acceptance criteria

- The top-level picker shows `Run job`, `Run workflow`, `Monitoring setup`;
  every existing `Run job` test passes unchanged.
- Parent catalog, `http-cart` → `http-orders` → `http-orders-status`,
  `5-iterations`, sizing on: the plan shows 7, 6, then `5-iterations`; three
  Compose runs in step order; step 1 writes `carts`; step 2 reads `carts`
  from step 1's `host_path` and writes `orders`; step 3 reads `orders`.
- The same workflow with `5-vu-5m`: step 1 at 3307 iterations and 14 VUs,
  step 2 at 2875 iterations and 12 VUs, step 3 at `5-vu-5m` (2500 rows
  needed: ceil(5 × 300 / 0.6)).
- Sizing off with `5-iterations`: every step's `--config` is
  `5-iterations.json`; every `steps[].sizing` is `null`.
- Sizing off with `Job default`: each step runs its own `spec.k6.config`; no
  step is sized.
- `Step 1:` lists only jobs with empty `requires` and at least one next job.
- `Step <n>:` lists only `next_jobs` candidates; `Done` appears from step 3;
  an empty step 3 or later ends the picking with the message.
- Step 2 fails: two Compose runs, step 3 `skipped`, exit code equals step
  2's k6 exit code, `outcome` is `failed`.
- `KeyboardInterrupt` in step 2: step 2 `interrupted`, step 3 `skipped`,
  `outcome` is `interrupted`, evidence written, exit 130.
- A writer short of rows: the `rows repeat` warning, the workflow continues,
  the exit code is unchanged.
- A pair `size_producer` rejects: the `not sized` warning; the step runs its
  own config.
- An optional dataset with no earlier writer runs on built-in data, even when
  a file with rows exists at its path.
- `n` at `Run these <n> steps?`: zero Compose runs, exit 0.
- A writer whose dataset file lies outside the reader's `data.directory`:
  the plan error, zero Compose runs, exit 1.
- No job or dataset literal is added to `src/punch/*.py`.

## Testing

- `tests/test_workflow.py` (new): catalogs written as temporary YAML with
  the parent repository's sizing values (`iterationSeconds` 1.1, 1.1, and
  0.6; `maxSeconds` 270; `margin` 0.15), so the expected numbers match the
  acceptance criteria. It covers `first_jobs`, `next_jobs`, linking, the
  three load kinds, chained sizing, both warnings, every error, and
  `run_workflow` with `execute_job` patched: Compose-run count and order,
  `data_overrides`, `optional_choices`, the shortfall warning, stop and skip,
  interruption, and the evidence fields.
- `tests/test_menu.py`: the new top-level entry, the step pickers with
  `Done` and the auto-stop, the sizing answer's loads, the single
  confirmation, and Esc. Scripted terminal and `TerminalMenu` as the
  existing tests do.
- Gates: `PYTHONPATH=src python3 -m unittest discover -s tests -p
  'test_*.py'`, `ruff check src tests`, `ruff format --check src tests`.
- Runtime evidence (rule 3 of `CLAUDE.md`): the bundled jobs declare no
  datasets, so the live run uses the parent repository. With its stack up
  (`./dev up`) and its k6 image built,
  `PYTHONPATH=vendor/punch/src python3 -m punch menu tests/performance/k6/jobs`,
  then `Run workflow`, `http-cart` → `http-orders` → `http-orders-status`,
  `5-iterations`, sizing on, writes `punch-workflow.json` with
  `"passed": true`, 7 `carts` rows, and 6 `orders` rows.

## Risks

- The margin compounds along the workflow: each earlier step adds its
  reader's margin on top of the rows its reader is already sized for (7
  carts for 5 status reads). Accepted: each margin covers its own writer's
  failed iterations; the cost is one margin factor of extra iterations per
  earlier step.
- A duration load sizes producers to thousands of iterations. For `5-vu-5m`,
  `estimated_seconds` puts step 1 at about 260 s and step 2 at about 264 s,
  before step 3's 300 s. These are estimates from the declared
  `iterationSeconds`. The plan prints the sized iterations and VUs before
  the confirmation.
- With data sizing off, every step runs one options file. Punch does not
  know the parent repository's `-browser` convention, so a workflow that
  mixes browser and HTTP steps can fail at k6 start. The plan names the file
  per step; such a workflow needs data sizing on or the parent repository's
  `./dev perf:workflow`.
- Until the parent repository delegates, two planners exist
  (`punch.workflow` and `scripts/pg/perf_workflow.py`) and can drift. The
  port keeps the planner's rules; the follow-up deletes the copy, and the
  parent repository's sizing tests (7, 6, 5 and 27, 23) become the parity
  check.
- The datasets a workflow writes stay in the data directory, where a later
  `Run job` can read them. The delete prompt at the end of the workflow
  offers to remove them.

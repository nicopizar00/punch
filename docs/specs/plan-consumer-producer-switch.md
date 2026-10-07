# Consumer Producer Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a selected consumer workflow is missing required data, Punch
offers (TTY only) to switch to the recommended producer before any Docker
prompt, and runs only that one workflow.

**Architecture:** Two pure helpers (`WorkflowCatalog.recommended_producers`,
`execution.missing_datasets`) plus one shared interactive helper
(`execution.offer_producer_switch`) returning a `ProducerSwitch` value. The
`punch` menu and `punch run` only wire inputs and act on that value.
`execute_workflow` stays non-interactive; its preflight is the backstop.
Every producer/consumer fact comes from workflow YAML through the catalog.

**Tech Stack:** Python 3.10+ standard library, PyYAML, simple-term-menu, rich
(menu only); `unittest`.

**Spec:** `docs/specs/spec-consumer-producer-switch.md` (saved beside it, same
precedent as the other `plan-*.md` files in `docs/specs/`).

## Global Constraints

- At most one `docker compose run` per invocation; the consumer never runs after a switch.
- "Enough data" = required dataset file exists with ≥1 row after the header (unchanged rule).
- Optional datasets (`spec.data.optional`) never trigger a switch.
- No workflow or dataset name literal in `src/punch/*.py`; all from YAML via the catalog.
- No new YAML schema fields.
- Prompts only when `stdin.isatty()`; otherwise current failure, exit 1, no Docker call.
- `all` selector never switches.
- `scripts/pg/k6runner.py` and `./dev perf:*` in the parent repo are untouched.
- `execute_workflow` gains no prompts.
- No AI attribution in commits.

## Review Focus

1. **Test runner with a real TTY on stdin** — `punch run` tests that leave `sys.stdin` unpatched would prompt and hang when run from a terminal; expected: CLI tests pin stdin to a non-TTY stream (Task 3, Step 1 setUp change).
2. **Producer that itself has missing required data two hops deep** — expected: chained offers, only the root producer runs (Task 2 chain test; Task 3 CLI chain test).
3. **Invalid pick input (`0`, `9`, `abc`, blank) in the multi-producer list** — expected: treated as cancel, no switch (Task 2 test `test_multi_producer_invalid_pick_cancels`).
4. **`--data` override pointing at a file with rows** — expected: no offer, consumer runs (Task 2 test `test_override_with_rows_is_not_missing`).
5. **Producer switched in, but its own required environment (`RUN_ID`) is missing** — expected: existing "missing required environment" failure for the producer, evidence still carries `switchedFrom` (Task 3 test `test_switched_producer_missing_env_reports_switch`).

## Test command

Host Python may lack `rich`; use a scratch venv once:

```bash
cd vendor/punch
python3 -m venv /tmp/punch-venv && /tmp/punch-venv/bin/pip install -q -r requirements.txt
PYTHONPATH=src /tmp/punch-venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Below, `PY` means `PYTHONPATH=src /tmp/punch-venv/bin/python`. Baseline: 131
tests pass under the venv.

---

### Task 1: `WorkflowCatalog.recommended_producers`

**Files:**
- Modify: `src/punch/catalog.py` (class `WorkflowCatalog`)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces: `WorkflowCatalog.recommended_producers(self, dataset: str, consumer: str) -> tuple[str, ...]` — producers of `dataset` whose product `targets` include `consumer`; if none, all `producers_of(dataset)`. Sorted by name.

- [ ] **Step 1: Write the failing tests** — append to `CatalogTests`:

```python
    def write_extra_producer(self, name: str, targets: str) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        text = text.replace("name: data-producer", f"name: {name}").replace(
            "targets: [data-consumer]", f"targets: [{targets}]"
        )
        (self.root / f"{name}.yaml").write_text(text, encoding="utf-8")

    def test_recommended_producers_prefers_those_targeting_the_consumer(self) -> None:
        self.write_extra_producer("aa-producer", "data-optional")
        self.write_extra_producer("zz-producer", "data-consumer")
        shutil.copy(self.root / "data-input.yaml", self.root / "data-optional.yaml")
        self.edit("data-optional.yaml", "name: data-consumer", "name: data-optional")
        self.edit("data-optional.yaml", "requires: [carts]", "optional: [carts]")
        catalog = load_catalog(self.root)
        self.assertEqual(
            catalog.recommended_producers("carts", "data-consumer"),
            ("data-producer", "zz-producer"),
        )

    def test_recommended_producers_falls_back_to_every_producer(self) -> None:
        catalog = load_catalog(self.root)
        self.assertEqual(
            catalog.recommended_producers("carts", "not-a-target"), ("data-producer",)
        )
        self.assertEqual(catalog.recommended_producers("unknown", "data-consumer"), ())
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_catalog -v`
Expected: 2 errors, `AttributeError: 'WorkflowCatalog' object has no attribute 'recommended_producers'`

- [ ] **Step 3: Implement** — add below `consumers_of` in `WorkflowCatalog`:

```python
    def recommended_producers(self, dataset: str, consumer: str) -> tuple[str, ...]:
        """Producers whose `targets` name `consumer`; every producer when none do."""
        producers = self.producers_of(dataset)
        targeting = tuple(
            name for name in producers
            if consumer in self.workflows[name].data.product(dataset).targets
        )
        return targeting or producers
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_catalog -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): recommend producers that target the consumer"
```

---

### Task 2: `missing_datasets` and `offer_producer_switch`

**Files:**
- Modify: `src/punch/execution.py` (near `preflight_requirements`)
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: `WorkflowCatalog.recommended_producers(dataset, consumer)` (Task 1); `WorkflowCatalog.workflows: Mapping[str, K6Workflow]`.
- Produces:
  - `missing_datasets(workflow: K6Workflow, overrides: Mapping[str, Path]) -> tuple[str, ...]`
  - `@dataclass(frozen=True) class ProducerSwitch: workflow: K6Workflow; produce: tuple[str, ...]; switched_from: tuple[str, ...]`
  - `offer_producer_switch(workflow: K6Workflow, catalog: WorkflowCatalog, overrides: Mapping[str, Path], *, stdin: IO[str], stdout: IO[str]) -> ProducerSwitch | None`
  - `switch_hint(switch: ProducerSwitch) -> str` → `"[punch] <produce[0]> ready; run <switched_from[0]> next."`
  - `preflight_requirements` signature unchanged; callers now pass `producers_of=partial(catalog.recommended_producers, consumer=workflow.name)`.

- [ ] **Step 1: Write the failing tests** — update the import block and add a
  new test class at the end of `tests/test_execution.py` (before
  `if __name__`, if present):

```python
from punch.catalog import load_catalog
from punch.execution import (
    ProducerSwitch,
    build_compose_run_command,
    execute_workflow,
    missing_datasets,
    offer_producer_switch,
    switch_hint,
)
```

```python
class ProducerSwitchTests(unittest.TestCase):
    """Catalog: data-producer → carts → data-consumer, plus a second hop
    data-consumer → orders → data-status written per test."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        fixtures = Path(__file__).resolve().parent / "fixtures"
        for name in ("docker-compose.yml", "data-output.yaml", "data-input.yaml"):
            shutil.copy(fixtures / name, self.root / name)
        self.carts = self.root / "data" / "carts.csv"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def add_second_hop(self) -> None:
        """data-consumer also produces orders for data-status."""
        self.edit(
            "data-input.yaml",
            "    requires: [carts]",
            "    produces:\n"
            "      - dataset: orders\n"
            "        columns: [orderId]\n"
            "        targets: [data-status]\n"
            "    requires: [carts]",
        )
        text = (self.root / "data-input.yaml").read_text(encoding="utf-8")
        status = text.replace("name: data-consumer", "name: data-status")
        status = status[: status.index("    produces:\n")] + "    requires: [orders]\n"
        (self.root / "data-status.yaml").write_text(status, encoding="utf-8")

    def write_carts(self, text: str) -> None:
        self.carts.parent.mkdir(parents=True, exist_ok=True)
        self.carts.write_text(text, encoding="utf-8")

    def offer(self, name: str, answers: str, overrides=None):
        catalog = load_catalog(self.root)
        stdout = io.StringIO()
        switch = offer_producer_switch(
            catalog.workflows[name], catalog, overrides or {},
            stdin=TtyInput(answers), stdout=stdout,
        )
        return switch, stdout.getvalue()

    def test_missing_datasets_lists_required_without_rows(self) -> None:
        consumer = load_catalog(self.root).workflows["data-consumer"]
        self.assertEqual(missing_datasets(consumer, {}), ("carts",))
        self.write_carts("cartId,productId,sid\n\n")
        self.assertEqual(missing_datasets(consumer, {}), ("carts",))
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(missing_datasets(consumer, {}), ())

    def test_optional_dataset_is_never_missing(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "optional: [carts]")
        consumer = load_catalog(self.root).workflows["data-consumer"]
        self.assertEqual(missing_datasets(consumer, {}), ())

    def test_override_with_rows_is_not_missing(self) -> None:
        alternate = self.root / "data" / "alt.csv"
        alternate.parent.mkdir(parents=True)
        alternate.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        switch, output = self.offer("data-consumer", "y\n", {"carts": alternate})
        self.assertIsNone(switch)
        self.assertEqual(output, "")

    def test_present_data_returns_none_without_prompt(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        switch, output = self.offer("data-consumer", "y\n")
        self.assertIsNone(switch)
        self.assertEqual(output, "")

    def test_non_tty_returns_none_without_prompt(self) -> None:
        catalog = load_catalog(self.root)
        stdout = io.StringIO()
        switch = offer_producer_switch(
            catalog.workflows["data-consumer"], catalog, {},
            stdin=io.StringIO("y\n"), stdout=stdout,
        )
        self.assertIsNone(switch)
        self.assertEqual(stdout.getvalue(), "")

    def test_accept_switches_to_recommended_producer(self) -> None:
        switch, output = self.offer("data-consumer", "y\n")
        self.assertEqual(switch.workflow.name, "data-producer")
        self.assertEqual(switch.produce, ("carts",))
        self.assertEqual(switch.switched_from, ("data-consumer",))
        self.assertIn('"data-consumer" requires "carts" (no rows at', output)
        self.assertIn("Switch to producer data-producer and write it? [y/N] ", output)
        self.assertEqual(switch_hint(switch), "[punch] carts ready; run data-consumer next.")

    def test_decline_returns_none(self) -> None:
        for answer in ("n\n", "\n", ""):
            with self.subTest(answer=answer):
                switch, _ = self.offer("data-consumer", answer)
                self.assertIsNone(switch)

    def test_chain_walks_to_root_producer(self) -> None:
        self.add_second_hop()
        switch, output = self.offer("data-status", "y\ny\n")
        self.assertEqual(switch.workflow.name, "data-producer")
        self.assertEqual(switch.produce, ("carts",))
        self.assertEqual(switch.switched_from, ("data-status", "data-consumer"))
        self.assertIn("Switch to producer data-consumer", output)
        self.assertIn("Switch to producer data-producer", output)
        self.assertEqual(switch_hint(switch), "[punch] carts ready; run data-status next.")

    def test_chain_stops_at_first_workflow_with_data(self) -> None:
        self.add_second_hop()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        switch, _ = self.offer("data-status", "y\n")
        self.assertEqual(switch.workflow.name, "data-consumer")
        self.assertEqual(switch.produce, ("orders",))
        self.assertEqual(switch.switched_from, ("data-status",))

    def test_declining_second_hop_returns_none(self) -> None:
        self.add_second_hop()
        switch, _ = self.offer("data-status", "y\nn\n")
        self.assertIsNone(switch)

    def test_multi_producer_pick(self) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        (self.root / "data-other.yaml").write_text(
            text.replace("name: data-producer", "name: data-other"), encoding="utf-8"
        )
        switch, output = self.offer("data-consumer", "2\n")
        self.assertIn("  1. data-other\n  2. data-producer\n", output)
        self.assertIn("Pick producer [1-2, Enter=cancel] ", output)
        self.assertEqual(switch.workflow.name, "data-producer")

    def test_multi_producer_invalid_pick_cancels(self) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        (self.root / "data-other.yaml").write_text(
            text.replace("name: data-producer", "name: data-other"), encoding="utf-8"
        )
        for answer in ("\n", "0\n", "3\n", "abc\n"):
            with self.subTest(answer=answer):
                switch, _ = self.offer("data-consumer", answer)
                self.assertIsNone(switch)

    def test_cycle_returns_none_and_reports(self) -> None:
        # data-producer now also requires orders, which data-consumer produces.
        self.add_second_hop()
        self.edit("data-output.yaml", "    produces:", "    requires: [orders]\n    produces:")
        self.edit("data-input.yaml", "targets: [data-status]", "targets: [data-status, data-producer]")
        switch, output = self.offer("data-status", "y\ny\ny\n")
        self.assertIsNone(switch)
        self.assertIn(
            "[punch] producer cycle: data-status → data-consumer → data-producer → data-consumer",
            output,
        )

    def test_producer_check_ignores_consumer_overrides(self) -> None:
        self.add_second_hop()
        empty = self.root / "data" / "empty-orders.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("orderId\n", encoding="utf-8")
        switch, _ = self.offer("data-status", "y\ny\n", {"orders": empty})
        self.assertEqual(switch.workflow.name, "data-producer")
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_execution -v`
Expected: import error `cannot import name 'ProducerSwitch'`

- [ ] **Step 3: Implement** — in `src/punch/execution.py`:

Add to imports:

```python
from typing import IO, TYPE_CHECKING, Callable, Mapping, Sequence
```

and after the `from punch.workflow import ...` line:

```python
if TYPE_CHECKING:
    from punch.catalog import WorkflowCatalog
```

Add after the `ExecutionResult` dataclass:

```python
@dataclass(frozen=True)
class ProducerSwitch:
    """The workflow to run instead of a consumer whose required data is missing."""

    workflow: K6Workflow
    produce: tuple[str, ...]
    switched_from: tuple[str, ...]
```

Replace `preflight_requirements` with `missing_datasets` + a thin
`preflight_requirements` (same message text):

```python
def missing_datasets(workflow: K6Workflow, overrides: Mapping[str, Path]) -> tuple[str, ...]:
    """Required datasets whose file is absent or has no data rows."""
    return tuple(
        dataset
        for dataset, path in required_data_paths(workflow, overrides).items()
        if not _has_data_rows(path)
    )


def preflight_requirements(
    workflow: K6Workflow,
    overrides: Mapping[str, Path],
    producers_of: Callable[[str], Sequence[str]],
) -> str | None:
    for dataset in missing_datasets(workflow, overrides):
        producers = ", ".join(producers_of(dataset)) or "no known workflow"
        return (
            f'{workflow.name} requires "{dataset}"; '
            f"produce it with: {producers} (--produce {dataset})"
        )
    return None


def _ask(question: str, stdin: IO[str], stdout: IO[str]) -> str:
    stdout.write(question)
    stdout.flush()
    return stdin.readline().strip()


def _choose_producer(
    producers: Sequence[str], *, stdin: IO[str], stdout: IO[str]
) -> str | None:
    if len(producers) == 1:
        answer = _ask(f"Switch to producer {producers[0]} and write it? [y/N] ", stdin, stdout)
        return producers[0] if answer.lower() in {"y", "yes"} else None
    for index, name in enumerate(producers, 1):
        stdout.write(f"  {index}. {name}\n")
    answer = _ask(f"Pick producer [1-{len(producers)}, Enter=cancel] ", stdin, stdout)
    if answer.isdigit() and 1 <= int(answer) <= len(producers):
        return producers[int(answer) - 1]
    return None


def offer_producer_switch(
    workflow: K6Workflow,
    catalog: WorkflowCatalog,
    overrides: Mapping[str, Path],
    *,
    stdin: IO[str],
    stdout: IO[str],
) -> ProducerSwitch | None:
    """Walk from a consumer with missing data to a producer whose own data is
    present, one confirmed hop at a time. Only a real terminal is asked."""
    if not missing_datasets(workflow, overrides) or not stdin.isatty():
        return None
    current, current_overrides = workflow, overrides
    visited = [workflow.name]
    produce: tuple[str, ...] = ()
    while missing := missing_datasets(current, current_overrides):
        dataset = missing[0]
        producers = catalog.recommended_producers(dataset, current.name)
        if not producers:
            return None
        path = required_data_paths(current, current_overrides)[dataset]
        stdout.write(f'"{current.name}" requires "{dataset}" (no rows at {path}).\n')
        chosen = _choose_producer(producers, stdin=stdin, stdout=stdout)
        if chosen is None:
            return None
        if chosen in visited:
            stdout.write(f"[punch] producer cycle: {' → '.join([*visited, chosen])}\n")
            return None
        visited.append(chosen)
        current, current_overrides, produce = catalog.workflows[chosen], {}, (dataset,)
    return ProducerSwitch(current, produce, tuple(visited[:-1]))


def switch_hint(switch: ProducerSwitch) -> str:
    return f"[punch] {switch.produce[0]} ready; run {switch.switched_from[0]} next."
```

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_execution tests.test_catalog -v`
Expected: all OK (existing preflight tests unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/punch/execution.py tests/test_execution.py
git commit -m "feat(execution): offer a producer switch for missing consumer data"
```

---

### Task 3: `punch run` integration

**Files:**
- Modify: `src/punch/__main__.py` (`_evidence_result`, `cmd_run`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `offer_producer_switch`, `switch_hint`, `ProducerSwitch` (Task 2); `WorkflowCatalog.recommended_producers` (Task 1).
- Produces: evidence result key `"switchedFrom": list[str]` on a switched run only.

- [ ] **Step 1: Write the failing tests** — in `tests/test_cli.py`:

Add `import io` to imports. Add a TTY stub after `PLAIN_WORKFLOW`:

```python
class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True
```

At the end of `CliTests.setUp`, pin stdin so a terminal-launched suite never prompts:

```python
        self.stdin_patch = patch("sys.stdin", io.StringIO())
        self.stdin_patch.start()
```

and in `tearDown`, first line:

```python
        self.stdin_patch.stop()
```

Add tests:

```python
    def run_with_answers(self, answers: str, *argv: str) -> tuple[int, str]:
        output = io.StringIO()
        with patch("sys.stdin", TtyInput(answers)), patch("sys.stdout", output):
            rc = main(["run", *argv])
        return rc, output.getvalue()

    def test_consumer_switch_runs_producer_once_and_records_switch(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        rc, output = self.run_with_answers("y\n", str(self.consumer_path))
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/data-producer.js", call)
        self.assertEqual(
            self.carts_path.read_text(encoding="utf-8"), "cartId,productId,sid\nc,p,s\n"
        )
        evidence = self.evidence()
        self.assertEqual(evidence["tests"], ["data-producer"])
        self.assertEqual(evidence["results"][0]["test"], "data-producer")
        self.assertEqual(evidence["results"][0]["switchedFrom"], ["data-consumer"])
        self.assertIn("[punch] carts ready; run data-consumer next.", output)

    def test_declined_switch_keeps_preflight_failure(self) -> None:
        rc, _ = self.run_with_answers("n\n", str(self.consumer_path))
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("produce it with: data-producer (--produce carts)", result["failure"])
        self.assertNotIn("switchedFrom", result)

    def test_switch_drops_consumer_flags_with_note(self) -> None:
        os.environ["RUN_ID"] = "run-1"
        os.environ["FAKE_DOCKER_STDOUT"] = "[DATA carts] c,p,s"
        empty = self.flows / "data" / "empty.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("cartId,productId,sid\n", encoding="utf-8")
        rc, output = self.run_with_answers(
            "y\n", str(self.consumer_path), "--data", "carts=data/empty.csv"
        )
        self.assertEqual(rc, 0)
        self.assertIn("[punch] ignoring --produce/--data for data-consumer", output)
        [call] = self.fake_docker_calls()
        self.assertFalse(any(argument.startswith("DATA_CARTS_CSV=") for argument in call))

    def test_switched_producer_missing_env_reports_switch(self) -> None:
        rc, _ = self.run_with_answers("y\n", str(self.consumer_path))
        self.assertEqual(rc, 1)
        self.assertEqual(self.compose_run_count(), 0)
        result = self.evidence()["results"][0]
        self.assertIn("missing required environment: RUN_ID", result["failure"])
        self.assertEqual(result["switchedFrom"], ["data-consumer"])

    def test_consumer_with_data_is_not_offered_a_switch(self) -> None:
        self.carts_path.parent.mkdir(parents=True)
        self.carts_path.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        rc, output = self.run_with_answers("y\n", str(self.consumer_path))
        self.assertEqual(rc, 0)
        self.assertNotIn("Switch to producer", output)
        self.assertNotIn("switchedFrom", self.evidence()["results"][0])

    def test_all_selector_never_offers_a_switch(self) -> None:
        with patch("punch.__main__.offer_producer_switch") as offer:
            self.run_with_answers("y\n", "all")
        offer.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_cli -v`
Expected: new tests FAIL (no prompt / no `switchedFrom`); `test_all_selector...` errors on missing attribute `offer_producer_switch` in `punch.__main__`.

- [ ] **Step 3: Implement** — in `src/punch/__main__.py`:

Add at module top-level imports (beside other top-level imports, so tests can patch it):

```python
from functools import partial

from punch.execution import offer_producer_switch, switch_hint
```

> If `__main__.py` keeps execution imports lazy to avoid import cost, put
> this import at module level anyway — `punch.execution` has no third-party
> dependencies.

Extend `_evidence_result`:

```python
def _evidence_result(
    workflow, result, *, skipped: bool = False, switched_from: tuple[str, ...] = ()
) -> dict:
    return {
        ...existing keys unchanged...,
        **({"skipped": True} if skipped else {}),
        **({"switchedFrom": list(switched_from)} if switched_from else {}),
    }
```

In `cmd_run`, add `from punch.catalog import CatalogError, load_catalog` is
already present. After the `runnable` loop and **before** the
`protected_paths` block, insert:

```python
    produce_args, data_args = list(args.produce), list(args.data)
    switch = None
    if args.selector != "all" and runnable:
        consumer = runnable[0]
        try:
            switch = offer_producer_switch(
                consumer,
                load_catalog(consumer.source_path.parent),
                resolve_data_overrides(consumer, data_args),
                stdin=sys.stdin,
                stdout=sys.stdout,
            )
        except (CatalogError, ValueError):
            switch = None  # reported by the per-workflow loop below
        if switch is not None:
            if produce_args or data_args:
                print(f"[punch] ignoring --produce/--data for {consumer.name}", flush=True)
            produce_args, data_args = list(switch.produce), []
            workflows = runnable = [switch.workflow]
    switched_from = switch.switched_from if switch is not None else ()
```

Then in the rest of `cmd_run` replace every `args.produce` with
`produce_args` and every `args.data` with `data_args` (the collision loop and
the per-workflow loop; the early `all` guard keeps `args.*`). Pass
`switched_from=switched_from` to every `_evidence_result(workflow, ...)` call
inside the per-workflow loop. Change
`producers_of=catalog.producers_of` to
`producers_of=partial(catalog.recommended_producers, consumer=workflow.name)`.
After `results.append(_evidence_result(...))` for the executed workflow add:

```python
        if result.passed and switch is not None:
            print(switch_hint(switch), flush=True)
```

Note `RUN_ID` missing for the producer: the `runnable` filter only skips
missing env for `all`, so the single-workflow path reaches
`execute_workflow`, which reports `missing required environment` — covered by
`test_switched_producer_missing_env_reports_switch`.

- [ ] **Step 4: Run to verify pass**

Run: `PY -m unittest tests.test_cli tests.test_execution tests.test_catalog -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add src/punch/__main__.py tests/test_cli.py
git commit -m "feat(run): switch a data-starved consumer to its producer"
```

---

### Task 4: `punch` menu integration

**Files:**
- Modify: `src/punch/menu.py` (`_choose_produce`, `_run_workflow_menu`, imports)
- Test: `tests/test_menu.py`

**Interfaces:**
- Consumes: `offer_producer_switch`, `preflight_requirements`, `switch_hint` (Task 2); `WorkflowCatalog.recommended_producers` (Task 1).
- Produces: `_choose_produce(workflow: K6Workflow, preselected: Sequence[str] = ()) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_menu.py`:

Add a scripted terminal next to `_ConfirmedTerminal`:

```python
class _ScriptedTerminal(io.StringIO):
    def isatty(self) -> bool:
        return True
```

Give `select_menu` an `answers` keyword (default keeps today's behavior):

```python
    @contextmanager
    def select_menu(self, *indices: int | None, answers: str = "yes\n"):
        """Supply terminal selections while retaining the real workflow/execution path."""
        with patch("sys.stdin", _ScriptedTerminal(answers)):
            with patch(
                "punch.menu.TerminalMenu",
                side_effect=[_SelectedMenu(index) for index in indices],
            ):
                yield
```

Make the existing `test_consumer_without_data_fails_before_docker` decline
explicitly (it previously relied on the single `yes` going to Docker):

```python
    def test_consumer_without_data_fails_before_docker(self) -> None:
        self.write_pair()
        stderr = io.StringIO()
        with self.select_menu(0, 0, answers="n\n"), patch("sys.stderr", stderr):
            rc = run_menu(self.root)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake_docker_calls(), [])
        self.assertIn(
            'consumer requires "orders"; produce it with: producer (--produce orders)',
            stderr.getvalue(),
        )
```

Add:

```python
    def test_switch_runs_producer_with_produce_preselected(self) -> None:
        self.write_workflow("producer", forward=["BASE_URL"], produces={"orders": ["consumer"]})
        self.write_workflow("consumer", requires=["orders"])
        output = io.StringIO()
        with patch.dict(os.environ, {"FAKE_DOCKER_STDOUT": "[DATA orders] 1"}):
            # menus: top-level, workflow (consumer=0), producer's base URL (current=0)
            with self.select_menu(0, 0, 0, answers="y\nyes\n"), patch("sys.stdout", output):
                with patch("builtins.input", return_value="") as prompt:
                    rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/producer.js", call)
        self.assertEqual(
            (self.root / "data" / "orders.csv").read_text(encoding="utf-8"), "id\n1\n"
        )
        self.assertIn('Write "orders" data for consumer? (Y/n)', prompt.call_args_list[0].args[0])
        text = output.getvalue()
        self.assertLess(text.index("Switch to producer producer"), text.index("Proceed?"))
        self.assertIn("[punch] orders ready; run consumer next.", text)

    def test_consumer_with_data_is_not_offered_a_switch(self) -> None:
        self.write_pair()
        (self.root / "data").mkdir()
        (self.root / "data" / "orders.csv").write_text("id\n1\n", encoding="utf-8")
        output = io.StringIO()
        with self.select_menu(0, 0), patch("sys.stdout", output):
            rc = run_menu(self.root)
        self.assertEqual(rc, 0)
        self.assertNotIn("Switch to producer", output.getvalue())
        [call] = self.fake_docker_calls()
        self.assertIn("/scripts/consumer.js", call)
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m unittest tests.test_menu -v`
Expected: `test_switch_runs_producer_with_produce_preselected` FAIL (consumer selected, no switch prompt); updated decline test FAIL on missing stderr message.

- [ ] **Step 3: Implement** — in `src/punch/menu.py`:

Imports:

```python
from functools import partial
from typing import List, Optional, Sequence

from punch.execution import (
    ...existing names...,
    offer_producer_switch,
    preflight_requirements,
    switch_hint,
)
```

`_choose_produce`:

```python
def _choose_produce(workflow: K6Workflow, preselected: Sequence[str] = ()) -> tuple[str, ...]:
    if workflow.data is None:
        return ()
    chosen = []
    for product in workflow.data.produces:
        selected = product.dataset in preselected
        answer = _prompt(
            f'Write "{product.dataset}" data for {", ".join(product.targets)}? '
            f'({"Y/n" if selected else "y/N"})',
            default="y" if selected else "n",
        )
        if answer.lower().startswith("y"):
            chosen.append(product.dataset)
    return tuple(chosen)
```

In `_run_workflow_menu`, right after the `try/except` that loads `workflow`
and `catalog`, insert:

```python
    switch = offer_producer_switch(workflow, catalog, {}, stdin=sys.stdin, stdout=sys.stdout)
    if switch is not None:
        workflow = switch.workflow
    producers_of = partial(catalog.recommended_producers, consumer=workflow.name)
    failure = preflight_requirements(workflow, {}, producers_of)
    if failure is not None:
        print(f"[punch] {failure}", file=sys.stderr)
        return 1
```

Change `produce = _choose_produce(workflow)` to
`produce = _choose_produce(workflow, switch.produce if switch else ())`.
Change `producers_of=catalog.producers_of` in `execute_workflow(...)` to
`producers_of=producers_of`. After `if result.passed: _print_metrics(workflow)`
add, inside the same `if`:

```python
        if switch is not None:
            print(switch_hint(switch))
```

- [ ] **Step 4: Run full suite**

Run: `PY -m unittest discover -s tests -p 'test_*.py'`
Expected: all OK (131 baseline + new)

- [ ] **Step 5: Commit**

```bash
git add src/punch/menu.py tests/test_menu.py
git commit -m "feat(menu): offer the producer switch before any Docker prompt"
```

---

### Task 5: Docs, literal check, parent pointer

**Files:**
- Modify: `AGENTS.md:49`, `README.md:48-50`, `docs/workflows/validation.md` (evidence schema near line 28; data section near line 65)
- Modify (parent repo): `tests/performance/k6/README.md:123-127`, submodule pointer `vendor/punch`

- [ ] **Step 1: Literal check** — no workflow/dataset names leaked into Python:

Run: `grep -nE "carts|orders|http-" src/punch/*.py`
Expected: only pre-existing hits (inspect each; none added by this branch — compare with `git diff main -- src/punch | grep -E '^\+.*(carts|orders|http-)'`, expected empty).

- [ ] **Step 2: `AGENTS.md:49`** — replace the line with:

```markdown
- A dataset is written only when the run opts in with `--produce <dataset>`; interactive prompts are limited to that opt-in, the producer-switch offer for a consumer whose required data is missing (TTY only, one Compose run), and the consumed-data delete prompt
```

- [ ] **Step 3: `README.md`** — after the "A consumer is preflighted before Docker…" bullet, add:

```markdown
- In a terminal, `punch run <consumer>` and the `punch` menu offer to switch
  to the recommended producer instead (producers whose `targets` name the
  consumer; otherwise every producer). Accepting runs that producer with
  `--produce <dataset>` — still one Compose run — and walks further when the
  producer is itself missing data. Re-run the consumer afterwards.
  Non-interactive runs keep failing before Docker.
```

- [ ] **Step 4: `docs/workflows/validation.md`** — in the evidence schema add
  under per-result keys: `"switchedFrom": ["<consumer>", ...]   // only when a producer switch replaced the selected workflow`; in the data section add the same paragraph as Step 3.

- [ ] **Step 5: Run full suite again**

Run: `PY -m unittest discover -s tests -p 'test_*.py'`
Expected: all OK

- [ ] **Step 6: Commit (submodule)**

```bash
git add AGENTS.md README.md docs/workflows/validation.md
git commit -m "docs: describe the consumer producer switch"
```

- [ ] **Step 7: Parent repo** — in `tests/performance/k6/README.md`, after
  "A consumer fails before Docker when a required dataset is missing or has no
  rows, naming the workflows that produce it." insert:
  "Through `punch run` or the `punch` menu in a terminal, Punch instead offers
  to run the recommended producer (with `--produce`) and tells you to re-run
  the consumer."

```bash
cd ../..
git add vendor/punch tests/performance/k6/README.md
git commit -m "chore: bump punch for the consumer producer switch"
```

(Branch first if on `main`; push only when asked.)

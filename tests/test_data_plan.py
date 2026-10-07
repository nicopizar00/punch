from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punch.catalog import load_catalog
from punch.data_plan import (
    CANCELED,
    Choice,
    DataPlan,
    PlanStop,
    plan_data,
    switch_hint,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ENV = {"RUN_ID": "1"}  # data-producer requires RUN_ID


class ScriptedChooser:
    """Records each Choice; answers with an option label (or None = Esc)."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.choices: list[Choice] = []

    def __call__(self, choice: Choice) -> int | None:
        self.choices.append(choice)
        answer = self.answers.pop(0)
        return None if answer is None else choice.options.index(answer)


class PlanDataTests(unittest.TestCase):
    """data-producer → carts → data-consumer; add_second_hop adds
    data-consumer → orders → data-status."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        for name in ("docker-compose.yml", "data-output.yaml", "data-input.yaml"):
            shutil.copy(FIXTURES / name, self.root / name)
        self.carts = self.root / "data" / "carts.csv"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def add_second_hop(self) -> None:
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

    def add_recommended_producer(self) -> None:
        text = (self.root / "data-output.yaml").read_text(encoding="utf-8")
        (self.root / "other-producer.yaml").write_text(
            text.replace("name: data-producer", "name: other-producer").replace(
                "targets: [data-consumer]", "targets: [data-consumer]\n        recommended: true"
            ),
            encoding="utf-8",
        )

    def make_optional(self) -> None:
        self.edit("data-input.yaml", "requires: [carts]", "optional: [carts]")

    def write_carts(self, text: str) -> None:
        self.carts.parent.mkdir(parents=True, exist_ok=True)
        self.carts.write_text(text, encoding="utf-8")

    def plan(self, name, chooser, overrides=None, choices=None, environment=ENV):
        catalog = load_catalog(self.root)
        return plan_data(
            catalog.workflows[name], catalog, overrides or {}, choices or {},
            choose=chooser, environment=environment,
        )

    # --- required data ---------------------------------------------------

    def test_present_required_data_asks_nothing(self) -> None:
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        chooser = ScriptedChooser()
        plan = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices, [])
        self.assertIsInstance(plan, DataPlan)
        self.assertEqual(plan.workflow.name, "data-consumer")
        self.assertEqual((plan.produce, plan.switched_from), ((), ()))

    def test_producer_picker_lists_all_with_recommended_preselected(self) -> None:
        self.add_recommended_producer()
        chooser = ScriptedChooser("data-producer")
        plan = self.plan("data-consumer", chooser)
        [choice] = chooser.choices
        self.assertEqual(
            choice.title,
            '"data-consumer" needs "carts" (no rows at data/carts.csv). '
            "Run a producer instead? (Esc cancels)",
        )
        self.assertEqual(choice.options, ("data-producer", "other-producer  (recommended)"))
        self.assertEqual(choice.cursor, 1)
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))
        self.assertEqual(plan.switched_from, ("data-consumer",))
        self.assertEqual(dict(plan.overrides), {})

    def test_producer_tags_missing_required_environment(self) -> None:
        self.add_recommended_producer()
        chooser = ScriptedChooser("data-producer  (needs RUN_ID)")
        self.plan("data-consumer", chooser, environment={})
        self.assertEqual(
            chooser.choices[0].options,
            ("data-producer  (needs RUN_ID)", "other-producer  (recommended, needs RUN_ID)"),
        )

    def test_producer_picker_without_recommendation_starts_first(self) -> None:
        chooser = ScriptedChooser("data-producer")
        self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices[0].options, ("data-producer",))
        self.assertEqual(chooser.choices[0].cursor, 0)

    def test_cancel_producer_picker_stops_with_preflight_detail(self) -> None:
        stop = self.plan("data-consumer", ScriptedChooser(None))
        self.assertEqual(
            stop,
            PlanStop(
                f'{CANCELED}; "data-consumer" needs "carts" (no rows at data/carts.csv) '
                "— produce it with: data-producer (--produce carts)",
                canceled=True,
            ),
        )

    def test_chain_walks_to_root_producer(self) -> None:
        self.add_second_hop()
        chooser = ScriptedChooser("data-consumer", "data-producer")
        plan = self.plan("data-status", chooser)
        self.assertEqual(len(chooser.choices), 2)
        self.assertTrue(chooser.choices[1].title.startswith('"data-consumer" needs "carts"'))
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))
        self.assertEqual(plan.switched_from, ("data-status", "data-consumer"))

    def test_chain_stops_at_first_workflow_with_data(self) -> None:
        self.add_second_hop()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        plan = self.plan("data-status", ScriptedChooser("data-consumer"))
        self.assertEqual(plan.workflow.name, "data-consumer")
        self.assertEqual(plan.produce, ("orders",))

    def test_cycle_stops(self) -> None:
        self.add_second_hop()
        self.edit("data-output.yaml", "    produces:", "    requires: [orders]\n    produces:")
        self.edit("data-input.yaml", "targets: [data-status]",
                  "targets: [data-status, data-producer]")
        chooser = ScriptedChooser("data-consumer", "data-producer", "data-consumer")
        stop = self.plan("data-status", chooser)
        self.assertEqual(
            stop,
            PlanStop("producer cycle: data-status → data-consumer → data-producer → data-consumer"),
        )

    def test_producer_step_ignores_selected_workflow_overrides(self) -> None:
        self.add_second_hop()
        empty = self.root / "data" / "empty-orders.csv"
        empty.parent.mkdir(parents=True)
        empty.write_text("orderId\n", encoding="utf-8")
        chooser = ScriptedChooser("data-consumer", "data-producer")
        plan = self.plan("data-status", chooser, overrides={"orders": empty})
        self.assertIn("no rows at data/carts.csv", chooser.choices[1].title)
        self.assertEqual(plan.workflow.name, "data-producer")

    # --- optional data ---------------------------------------------------

    def test_source_picker_with_default_first(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\nd,q,t\n")
        chooser = ScriptedChooser("default (built-in)")
        plan = self.plan("data-consumer", chooser)
        [choice] = chooser.choices
        self.assertEqual(
            choice.title, '"data-consumer" can read "carts" — pick a source (Esc cancels)'
        )
        self.assertEqual(choice.options, ("default (built-in)", "data/carts.csv (2 rows)"))
        self.assertEqual(choice.cursor, 0)
        self.assertEqual(dict(plan.optional_choices), {"carts": False})

    def test_source_picker_file_with_rows(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        plan = self.plan("data-consumer", ScriptedChooser("data/carts.csv (1 rows)"))
        self.assertEqual(dict(plan.optional_choices), {"carts": True})

    def test_optional_file_without_rows_offers_producers(self) -> None:
        self.make_optional()
        chooser = ScriptedChooser("data/carts.csv (no rows)", "data-producer")
        plan = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices[0].options[1], "data/carts.csv (no rows)")
        self.assertEqual(plan.workflow.name, "data-producer")
        self.assertEqual(plan.produce, ("carts",))

    def test_optional_without_rows_or_producer_defaults_silently(self) -> None:
        self.make_optional()
        (self.root / "data-output.yaml").unlink()
        chooser = ScriptedChooser()
        plan = self.plan("data-consumer", chooser)
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.optional_choices), {"carts": False})

    def test_presets_skip_source_picker(self) -> None:
        self.make_optional()
        alternate = self.root / "data" / "alt.csv"
        alternate.parent.mkdir(parents=True)
        alternate.write_text("cartId,productId,sid\nc,p,s\n", encoding="utf-8")
        chooser = ScriptedChooser()
        plan = self.plan("data-consumer", chooser, overrides={"carts": alternate})
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.overrides), {"carts": alternate})
        plan = self.plan("data-consumer", chooser, choices={"carts": False})
        self.assertEqual(chooser.choices, [])
        self.assertEqual(dict(plan.optional_choices), {"carts": False})

    def test_cancel_source_picker(self) -> None:
        self.make_optional()
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(
            self.plan("data-consumer", ScriptedChooser(None)), PlanStop(CANCELED, canceled=True)
        )

    # --- hint ------------------------------------------------------------

    def test_switch_hint_names_origin_and_what_it_still_misses(self) -> None:
        self.add_second_hop()
        catalog = load_catalog(self.root)
        plan = DataPlan(catalog.workflows["data-producer"], produce=("carts",),
                        switched_from=("data-status", "data-consumer"))
        self.write_carts("cartId,productId,sid\nc,p,s\n")
        self.assertEqual(
            switch_hint(plan, catalog),
            "[punch] carts ready; run data-status next (still missing: orders).",
        )
        plan = DataPlan(catalog.workflows["data-producer"], produce=("carts",),
                        switched_from=("data-consumer",))
        self.assertEqual(switch_hint(plan, catalog), "[punch] carts ready; run data-consumer next.")


if __name__ == "__main__":
    unittest.main()

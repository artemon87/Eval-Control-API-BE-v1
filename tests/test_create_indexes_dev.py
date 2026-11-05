import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.create_indexes_dev import (
    TARGET_DATABASE,
    arguments,
    desired_indexes,
    main,
    plan_collection,
    reconcile,
)


def dev_inventory():
    result = {}
    missing = {"evaluation_time_window", "unit_skill_insight_window", "unit_configuration_history"}
    for collection, models in desired_indexes().items():
        result[collection] = []
        for model in models:
            spec = copy.deepcopy(dict(model.document))
            if spec["name"] in missing:
                continue
            if spec["name"] == "run_id_unique":
                spec["name"] = "run_id_1"
            spec["hidden"] = True
            result[collection].append(spec)
    return result


class Cursor:
    def __init__(self, values):
        self.values = values

    async def to_list(self, length=None):
        return copy.deepcopy(self.values)


class Database:
    def __init__(self):
        self.name = TARGET_DATABASE
        self.inventory = dev_inventory()
        self.writes = []

    async def list_collection_names(self):
        return list(self.inventory)

    def __getitem__(self, name):
        def indexes():
            return Cursor(self.inventory[name])

        async def create(models):
            self.writes.extend((name, model.document["name"]) for model in models)
            self.inventory[name].extend(copy.deepcopy(dict(model.document)) for model in models)

        return SimpleNamespace(list_indexes=indexes, create_indexes=create)


class IndexTests(unittest.TestCase):
    def test_dev_snapshot_has_exactly_four_missing_indexes(self):
        db = Database()
        before = copy.deepcopy(db.inventory)
        asyncio.run(reconcile(db))
        self.assertEqual(db.writes, [])
        self.assertEqual(db.inventory, before)
        asyncio.run(reconcile(db, apply=True))
        self.assertEqual(
            db.writes,
            [
                ("unit_eval_runs", "evaluation_time_window"),
                ("unit_eval_runs", "unit_skill_insight_window"),
                ("unit_eval_runs", "unit_configuration_history"),
                ("e2e_eval_runs", "evaluation_time_window"),
            ],
        )
        for collection, specs in before.items():
            for spec in specs:
                self.assertIn(spec, db.inventory[collection])
        asyncio.run(reconcile(db, apply=True))
        self.assertEqual(len(db.writes), 4)

    def test_late_conflict_blocks_all_writes(self):
        db = Database()
        db.inventory["e2e_eval_cases"][0]["key"] = {"wrong": 1}
        with self.assertRaises(RuntimeError):
            asyncio.run(reconcile(db, apply=True))
        self.assertEqual(db.writes, [])

    def test_missing_unique_index_blocks_creation(self):
        db = Database()
        db.inventory["unit_eval_runs"] = [
            s for s in db.inventory["unit_eval_runs"] if not s.get("unique")
        ]
        with self.assertRaisesRegex(RuntimeError, "required existing unique"):
            asyncio.run(reconcile(db, apply=True))
        self.assertEqual(db.writes, [])

    def test_option_conflicts_are_not_equivalent(self):
        model = desired_indexes()["unit_eval_cases"][0]
        for option in [
            {"unique": True},
            {"partialFilterExpression": {"x": 1}},
            {"collation": {"locale": "en"}},
        ]:
            spec = dict(model.document, **option)
            _, _, errors = plan_collection("cases", [model], [spec])
            self.assertTrue(errors)

    def test_key_order_is_significant(self):
        model = desired_indexes()["unit_eval_cases"][0]
        spec = dict(model.document, key=dict(reversed(list(model.document["key"].items()))))
        self.assertTrue(plan_collection("cases", [model], [spec])[2])

    def test_wrong_db_and_missing_collection_block_writes(self):
        for wrong_db in (False, True):
            db = Database()
            if wrong_db:
                db.name = "staging"
            else:
                db.inventory.pop("e2e_eval_cases")
            with self.assertRaises(RuntimeError):
                asyncio.run(reconcile(db, apply=True))
            self.assertEqual(db.writes, [])

    def test_apply_requires_exact_confirmation(self):
        for args in [["--apply"], ["--apply", "--confirm-database", "staging"]]:
            with self.assertRaises(SystemExit):
                arguments(args)
        self.assertFalse(arguments([]).apply)
        self.assertTrue(arguments(["--apply", "--confirm-database", TARGET_DATABASE]).apply)

    def test_missing_uri_never_connects(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("scripts.create_indexes_dev.load_dotenv"),
            patch("scripts.create_indexes_dev.AsyncMongoClient") as client,
        ):
            with self.assertRaisesRegex(RuntimeError, "Set MONGODB_URI"):
                asyncio.run(main(arguments([])))
            client.assert_not_called()

    def test_conflicting_env_never_connects(self):
        for env in [
            {"MONGODB_URI": "mongodb://one", "DS_DB_MONGODB_URI": "mongodb://two"},
            {"MONGODB_URI": "mongodb://one", "MONGODB_DATABASE": "bryte_evals"},
        ]:
            with (
                patch.dict("os.environ", env, clear=True),
                patch("scripts.create_indexes_dev.load_dotenv"),
                patch("scripts.create_indexes_dev.AsyncMongoClient") as client,
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(main(arguments([])))
                client.assert_not_called()

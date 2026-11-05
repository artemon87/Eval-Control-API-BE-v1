import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.dummy_unit_data import CASES, RUNS, arguments, execute, generate, main, selector


class DummyTests(unittest.TestCase):
    def args(self, *extra):
        return arguments(["seed", "--database", "local", "--batch-id", "perf-test", *extra])

    def test_full_default_dataset_is_consistent(self):
        args = self.args()
        anchor = datetime(2026, 11, 5, tzinfo=UTC)
        ids, branches, skills, outcomes, histories = set(), set(), set(), set(), {}
        count = 0
        for run, cases in generate(args, anchor):
            count += 1
            self.assertNotIn(run["_id"], ids)
            ids.add(run["_id"])
            self.assertEqual(run["unit_config"]["category"], "dummy")
            self.assertEqual(run["unit_config"]["skill_ids"], [run["skill"]])
            self.assertEqual(run["summary"]["total"], len(cases))
            passed = sum(case["verdict"] == "passed" for case in cases)
            self.assertEqual(run["summary"]["passed"], passed)
            self.assertEqual(run["summary"]["failed"], len(cases) - passed)
            self.assertEqual(run["verdict"], "passed" if passed / len(cases) >= 0.8 else "failed")
            self.assertLess(run["finished_at"], anchor)
            branches.add(run["git_ref"])
            skills.add(run["skill"])
            outcomes.add(run["verdict"])
            for case in cases:
                self.assertEqual(case["run_id"], run["run_id"])
                self.assertNotIn(case["_id"], ids)
                ids.add(case["_id"])
                self.assertEqual(case["category"], "dummy")
                self.assertEqual(
                    case["verdict"] == "passed",
                    all(
                        value >= case["thresholds"][metric]
                        for metric, value in case["scores"].items()
                    ),
                )
                histories.setdefault((case["skill"], case["case_id"]), set()).add(case["verdict"])
        self.assertEqual(count, 5000)
        self.assertEqual(len(ids), 30000)
        self.assertEqual(len(branches), 10)
        self.assertEqual(len(skills), 5)
        self.assertEqual(outcomes, {"passed", "failed"})
        self.assertTrue(all(values == {"passed", "failed"} for values in histories.values()))

    def test_repeatable_and_batch_isolated(self):
        anchor = datetime.now(UTC)
        one = list(generate(self.args("--runs", "10"), anchor))
        self.assertEqual(one, list(generate(self.args("--runs", "10"), anchor)))
        other = self.args("--runs", "10")
        other.batch_id = "another"
        self.assertNotEqual(one[0][0]["_id"], next(generate(other, anchor))[0]["_id"])

    def test_preview_never_connects(self):
        with patch("scripts.dummy_unit_data.AsyncMongoClient") as client:
            asyncio.run(main(self.args()))
            client.assert_not_called()

    def test_confirmation_and_cleanup_scope(self):
        for argv in [
            ["seed", "--database", "local", "--apply"],
            ["cleanup", "--database", "local"],
        ]:
            with self.assertRaises(SystemExit):
                arguments(argv)
        self.assertEqual(
            selector("perf-test"),
            {"dummy_data": True, "dummy_batch_id": "perf-test", "category": "dummy"},
        )

    def test_cleanup_uses_three_markers_and_cases_first(self):
        queries = []

        class DB:
            async def list_collection_names(self):
                return [RUNS, CASES]

            def __getitem__(self, name):
                async def delete(query):
                    queries.append((name, query))
                    return SimpleNamespace(deleted_count=5)

                return SimpleNamespace(
                    count_documents=AsyncMock(return_value=5), delete_many=delete
                )

        args = arguments(["cleanup", "--database", "local", "--batch-id", "perf-test"])
        asyncio.run(execute(DB(), args, datetime.now(UTC)))
        self.assertEqual(queries, [])
        args.apply = True
        asyncio.run(execute(DB(), args, datetime.now(UTC)))
        self.assertEqual(queries, [(CASES, selector("perf-test")), (RUNS, selector("perf-test"))])

    def test_existing_batch_refused_and_inserts_are_bounded(self):
        writes = []

        class DB:
            async def list_collection_names(self):
                return [RUNS, CASES]

            def __getitem__(self, name):
                async def insert(docs, ordered):
                    writes.append((name, len(docs)))

                return SimpleNamespace(
                    count_documents=AsyncMock(return_value=0), insert_many=insert
                )

        asyncio.run(execute(DB(), self.args("--runs", "205"), datetime.now(UTC)))
        self.assertEqual(
            writes, [(CASES, 500), (RUNS, 100), (CASES, 500), (RUNS, 100), (CASES, 25), (RUNS, 5)]
        )
        with self.assertRaises(RuntimeError):

            class Existing(DB):
                def __getitem__(self, name):
                    return SimpleNamespace(count_documents=AsyncMock(return_value=1))

            asyncio.run(execute(Existing(), self.args(), datetime.now(UTC)))

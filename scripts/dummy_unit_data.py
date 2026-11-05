"""Generate or remove isolated synthetic unit evaluations; preview by default."""

import argparse
import asyncio
import hashlib
import os
import random
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

RUNS = "unit_eval_runs"
CASES = "unit_eval_cases"
METRICS = ("InstructionFollowing", "Text Quality")


def identity(kind, batch, number):
    return ObjectId(hashlib.sha256(f"{kind}:{batch}:{number}".encode()).hexdigest()[:24])


def generate(args, anchor):
    rng = random.Random(args.seed)
    end = anchor - timedelta(minutes=30)
    for i in range(args.runs):
        skill_number = i % args.skills
        # Cycle every branch through every skill rather than tying one to another.
        branch_number = (i // args.skills) % args.branches
        skill = f"dummy-skill-{skill_number + 1:02d}"
        run_id = f"unit-dummy-{args.batch_id}-{i + 1:06d}"
        start = end - (timedelta(days=args.days) - timedelta(minutes=30)) * (
            (args.runs - 1 - i) / args.runs
        )
        case_start = start
        cases = []
        for j in range(args.cases_per_run):
            # Branch-dependent probabilities create fixes/regressions across branches.
            passed = rng.random() < (0.45 + 0.5 * branch_number / max(1, args.branches - 1))
            scores = {metric: round(rng.uniform(4.05, 5), 2) for metric in METRICS}
            if not passed:
                scores[METRICS[j % len(METRICS)]] = round(rng.uniform(1, 3.95), 2)
            duration = rng.randint(1000, 12000)
            finish = case_start + timedelta(milliseconds=duration)
            case_id = f"dummy-case-{j + 1:02d}"
            multi = j % 2 == 1
            cases.append(
                {
                    "_id": identity("case", args.batch_id, i * args.cases_per_run + j),
                    "case_id": case_id,
                    "run_id": run_id,
                    "batch_id": args.batch_id,
                    "eval_type": "unit",
                    "skill": skill,
                    "test_name": f"{skill}-{case_id}",
                    "test_type": "multi-turn" if multi else "single-turn",
                    "tier": None,
                    "verdict": "passed" if passed else "failed",
                    "scores": scores,
                    "thresholds": dict.fromkeys(METRICS, 4),
                    "score_explanations": dict.fromkeys(METRICS, "Synthetic benchmark score."),
                    "tool_calls": [],
                    "validation_result": None,
                    "prompt": "Synthetic evaluation prompt.",
                    "expected_response": "Synthetic successful response.",
                    "expected_trajectory": [],
                    "predicted_trajectory": [],
                    "turns": [
                        {
                            "turn_number": 1,
                            "tool_calls": [],
                            "response": "Synthetic turn.",
                            "genai_scores": scores,
                        }
                    ]
                    if multi
                    else None,
                    "response": "Synthetic response.",
                    "error": None,
                    "started_at": case_start,
                    "finished_at": finish,
                    "duration_ms": duration,
                    "evalset_id": skill,
                    "evaluation_layer": "llm-judge",
                    "skill_version": "1.0.0",
                    "bsa_version": "1.19.0",
                    "agent_name": "dummy_suite_agent",
                    "environment": "develop",
                    "category": "dummy",
                    "stage": "1-dev-staging",
                    "target_environment": "dummy-dev-target",
                    "case_meta": {
                        "category": "dummy",
                        "stage": "1-dev-staging",
                        "target_environment": "dummy-dev-target",
                        "agent_name": "dummy_suite_agent",
                        "skill_version": "1.0.0",
                        "bsa_version": "1.19.0",
                    },
                    "dummy_data": True,
                    "dummy_batch_id": args.batch_id,
                }
            )
            case_start = finish
        passed_count = sum(case["verdict"] == "passed" for case in cases)
        mean_scores = {
            metric: round(sum(c["scores"][metric] for c in cases) / len(cases), 4)
            for metric in METRICS
        }
        run = {
            "_id": identity("run", args.batch_id, i),
            "run_id": run_id,
            "batch_id": args.batch_id,
            "eval_type": "unit",
            "execution_status": "completed",
            "verdict": "passed" if passed_count / len(cases) >= 0.8 else "failed",
            "skill": skill,
            "environment": "develop",
            "unit_config": {
                "skill_ids": [skill],
                "evalsets": [skill],
                "mode": "all",
                "metrics": list(METRICS),
                "bsa_environment": "develop",
                "bsa_version": "1.19.0",
                "skill_version": "1.0.0",
                "pass_rate_threshold": 0.8,
                "evaluation_layer": "llm-judge",
                "category": "dummy",
                "stage": "1-dev-staging",
                "target_environment": "dummy-dev-target",
                "agent_name": "dummy_suite_agent",
            },
            "created_at": start,
            "started_at": start,
            "finished_at": case_start,
            "duration_ms": (case_start - start).total_seconds() * 1000,
            "trigger": "cli",
            "actor": args.actor,
            "git_sha": hashlib.sha1(run_id.encode(), usedforsecurity=False).hexdigest(),
            "git_ref": f"dummy/perf-branch-{branch_number + 1:02d}",
            "github_repository": "dummy/evalhub-performance",
            "github_run_id": None,
            "github_run_attempt": None,
            "github_event": None,
            "summary": {
                "total": len(cases),
                "passed": passed_count,
                "failed": len(cases) - passed_count,
                "skipped": 0,
                "pass_rate_pct": round(100 * passed_count / len(cases), 2),
                "mean_scores": mean_scores,
                "mean_score": round(sum(mean_scores.values()) / len(METRICS), 4),
                "by_tier": None,
            },
            "category": "dummy",
            "dummy_data": True,
            "dummy_batch_id": args.batch_id,
        }
        yield run, cases


def selector(batch):
    return {"dummy_data": True, "dummy_batch_id": batch, "category": "dummy"}


async def execute(db, args, anchor):
    existing = set(await db.list_collection_names())
    if not {RUNS, CASES} <= existing:
        raise RuntimeError("Expected unit collections missing; refusing to create collections")
    query = selector(args.batch_id)
    counts = [await db[name].count_documents(query) for name in (RUNS, CASES)]
    print(f"Batch {args.batch_id}: {counts[0]} existing runs; {counts[1]} existing cases")
    if args.action == "cleanup":
        if not args.apply:
            print("PREVIEW: no records deleted.")
            return
        # Delete cases first; interrupted cleanup can safely be rerun.
        for name in (CASES, RUNS):
            result = await db[name].delete_many(query)
            print(f"Deleted {result.deleted_count} from {name}")
        return
    if any(counts):
        raise RuntimeError("Batch already exists; use a new batch ID or clean up this batch first")
    runs_buffer, cases_buffer = [], []
    for run, cases in generate(args, anchor):
        runs_buffer.append(run)
        cases_buffer.extend(cases)
        if len(runs_buffer) >= 100:
            await db[CASES].insert_many(cases_buffer, ordered=True)
            await db[RUNS].insert_many(runs_buffer, ordered=True)
            runs_buffer, cases_buffer = [], []
    if runs_buffer:
        await db[CASES].insert_many(cases_buffer, ordered=True)
        await db[RUNS].insert_many(runs_buffer, ordered=True)
    print("Seed complete. No real records or indexes were modified.")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed", "cleanup"])
    parser.add_argument("--database", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--batch-id")
    parser.add_argument("--runs", type=int, default=5000)
    parser.add_argument("--cases-per-run", type=int, default=5)
    parser.add_argument("--branches", type=int, default=10)
    parser.add_argument("--skills", type=int, default=5)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--actor", default="artem.kovtunenko")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args(argv)
    if args.apply and args.confirm_database != args.database:
        parser.error("--apply requires --confirm-database matching --database")
    if args.action == "cleanup" and not args.batch_id:
        parser.error("cleanup requires --batch-id")
    args.batch_id = args.batch_id or f"perf-{uuid4().hex[:12]}"
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", args.batch_id):
        parser.error("batch ID must be 1–64 letters, digits or hyphens")
    if not (
        1 <= args.runs <= 100000
        and 1 <= args.cases_per_run <= 100
        and 1 <= args.branches <= 100
        and 1 <= args.skills <= 100
        and 1 <= args.days <= 365
    ):
        parser.error("Invalid generation bounds; see script limits")
    return args


async def main(args):
    anchor = datetime.now(UTC)
    if args.action == "seed":
        print(
            f"{args.runs} runs + {args.runs * args.cases_per_run} cases; "
            f"{args.branches} branches; category=dummy; batch={args.batch_id}"
        )
        if not args.apply:
            first, _ = next(generate(args, anchor))
            print(
                f"PREVIEW ONLY: database={args.database}; "
                f"first run={first['run_id']}; no connection made"
            )
            return
    load_dotenv(args.env_file, override=False)
    uri = os.getenv("MONGODB_URI") or os.getenv("DS_DB_MONGODB_URI")
    if not uri:
        raise RuntimeError("Set MONGODB_URI or DS_DB_MONGODB_URI; no localhost fallback")
    if (
        os.getenv("MONGODB_URI")
        and os.getenv("DS_DB_MONGODB_URI")
        and os.environ["MONGODB_URI"] != os.environ["DS_DB_MONGODB_URI"]
    ):
        raise RuntimeError("Conflicting MongoDB URIs")
    for name in ("MONGODB_DATABASE", "DS_DB_MONGODB_NAME"):
        if os.getenv(name) and os.environ[name] != args.database:
            raise RuntimeError(f"{name} differs from --database")
    client = AsyncMongoClient(
        uri, appname="evalhub-dummy-data", maxPoolSize=2, serverSelectionTimeoutMS=5000
    )
    try:
        await client.admin.command("ping")
        print(f"Database={args.database}; nodes={sorted(client.nodes)}")
        await execute(client[args.database], args, anchor)
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main(arguments()))
    except PyMongoError as error:
        raise SystemExit(
            f"Database operation failed: {type(error).__name__}, "
            f"code={getattr(error, 'code', None)}. Partial data may remain; "
            "use cleanup with the printed batch ID."
        ) from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None

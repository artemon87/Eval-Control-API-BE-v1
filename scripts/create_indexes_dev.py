import argparse
import asyncio
import os

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel
from pymongo.errors import PyMongoError

TARGET_DATABASE = "agent-evals-east4-dev"


def desired_indexes():
    return {
        "unit_eval_runs": [
            IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("started_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="evaluation_time_window",
            ),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("skill", ASCENDING),
                    ("started_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="unit_skill_insight_window",
            ),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("skill", ASCENDING),
                    ("environment", ASCENDING),
                    ("unit_config.skill_version", ASCENDING),
                    ("started_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="skill_version_history",
            ),
            IndexModel(
                [("verdict", ASCENDING), ("started_at", DESCENDING), ("_id", DESCENDING)],
                name="verdict_history",
            ),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("unit_config.category", ASCENDING),
                    ("unit_config.target_environment", ASCENDING),
                    ("unit_config.agent_name", ASCENDING),
                    ("started_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="unit_configuration_history",
            ),
        ],
        "unit_eval_cases": [
            IndexModel([("run_id", ASCENDING), ("_id", DESCENDING)], name="run_cases"),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("skill", ASCENDING),
                    ("case_id", ASCENDING),
                    ("run_id", ASCENDING),
                ],
                name="case_history",
            ),
        ],
        "e2e_eval_runs": [
            IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("started_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="evaluation_time_window",
            ),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("stage", ASCENDING),
                    ("target", ASCENDING),
                    ("started_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="target_history",
            ),
            IndexModel(
                [("batch_id", ASCENDING), ("started_at", DESCENDING)],
                name="batch_history",
            ),
        ],
        "e2e_eval_cases": [
            IndexModel([("run_id", ASCENDING), ("_id", DESCENDING)], name="run_cases"),
            IndexModel(
                [
                    ("eval_type", ASCENDING),
                    ("suite", ASCENDING),
                    ("case_id", ASCENDING),
                    ("run_id", ASCENDING),
                ],
                name="case_history",
            ),
        ],
    }


def signature(spec):
    """Visibility and names are intentionally not part of index equivalence."""
    options = {
        k: v for k, v in spec.items() if k not in {"key", "name", "v", "ns", "hidden", "background"}
    }
    for flag in ("unique", "sparse"):
        if not options.get(flag):
            options.pop(flag, None)
    return list(spec["key"].items()), options


def plan_collection(collection, desired, existing):
    missing, messages, conflicts = [], [], []
    for model in desired:
        wanted = model.document
        named = next((item for item in existing if item["name"] == wanted["name"]), None)
        equivalent = next((item for item in existing if signature(item) == signature(wanted)), None)
        if named is not None and signature(named) != signature(wanted):
            conflicts.append(f"{collection}: incompatible index named {wanted['name']}")
        elif equivalent is not None:
            visibility = (
                "hidden; preserving hidden state" if equivalent.get("hidden") else "visible"
            )
            messages.append(f"KEEP {collection}.{equivalent['name']} ({visibility})")
        elif any(list(item["key"].items()) == list(wanted["key"].items()) for item in existing):
            conflicts.append(f"{collection}: incompatible options for {wanted['name']}")
        elif wanted.get("unique"):
            # This dev database already has uniqueness. Never silently introduce
            # a new write constraint if its expected index has disappeared.
            conflicts.append(f"{collection}: required existing unique run-ID index missing")
        else:
            messages.append(f"CREATE {collection}.{wanted['name']}")
            missing.append(model)
    return missing, messages, conflicts


async def reconcile(database, apply=False):
    if database.name != TARGET_DATABASE:
        raise RuntimeError("Unexpected database; no changes made")
    desired = desired_indexes()
    collections = set(await database.list_collection_names())
    absent = set(desired) - collections
    if absent:
        raise RuntimeError(f"Required collections missing: {', '.join(sorted(absent))}")
    plans, conflicts = {}, []
    # Complete preflight across all four collections before the first write.
    for collection, models in desired.items():
        existing = await database[collection].list_indexes().to_list(length=None)
        missing, messages, errors = plan_collection(collection, models, existing)
        plans[collection] = missing
        conflicts.extend(errors)
        for message in messages:
            print(message)
    if conflicts:
        raise RuntimeError("Preflight blocked; no changes made:\n" + "\n".join(conflicts))
    total = sum(len(models) for models in plans.values())
    print(f"{total} missing index(es). Existing names and visibility will be preserved.")
    if not apply:
        print("PLAN ONLY: no indexes changed. Use --apply --confirm-database " + TARGET_DATABASE)
        return
    for collection, models in plans.items():
        for model in models:
            print(f"BUILD {collection}.{model.document['name']}", flush=True)
            await database[collection].create_indexes([model])
            actual = await database[collection].list_indexes().to_list(length=None)
            if not any(signature(item) == signature(model.document) for item in actual):
                raise RuntimeError("Post-build verification failed; stop and inspect indexes")
            print(f"OK {collection}.{model.document['name']}", flush=True)
    print("Done. No indexes dropped, renamed, or unhidden.")


def arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="Plan/apply missing EvalHub indexes for the east4 dev DB only"
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args(argv)
    if args.apply and args.confirm_database != TARGET_DATABASE:
        parser.error("--apply requires --confirm-database " + TARGET_DATABASE)
    return args


async def main(args):
    load_dotenv(args.env_file, override=False)
    uri = os.environ.get("MONGODB_URI") or os.environ.get("DS_DB_MONGODB_URI")
    if not uri:
        raise RuntimeError("Set MONGODB_URI (or DS_DB_MONGODB_URI); there is no localhost fallback")
    if (
        os.environ.get("MONGODB_URI")
        and os.environ.get("DS_DB_MONGODB_URI")
        and os.environ["MONGODB_URI"] != os.environ["DS_DB_MONGODB_URI"]
    ):
        raise RuntimeError("Conflicting MongoDB URI environment variables")
    for name in ("MONGODB_DATABASE", "DS_DB_MONGODB_NAME"):
        if os.environ.get(name) and os.environ[name] != TARGET_DATABASE:
            raise RuntimeError(f"{name} does not match {TARGET_DATABASE}")
    client = AsyncMongoClient(
        uri, appname="eval-control-indexer", serverSelectionTimeoutMS=5000, maxPoolSize=2
    )
    try:
        await client.admin.command("ping")
        print(f"Database: {TARGET_DATABASE}; connected nodes: {sorted(client.nodes)}")
        await reconcile(client[TARGET_DATABASE], apply=args.apply)
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main(arguments()))
    except PyMongoError as error:
        # Do not echo connection strings or authentication details from exceptions.
        raise SystemExit(
            f"MongoDB operation failed: {type(error).__name__}, "
            f"code={getattr(error, 'code', None)}. "
            "Inspect server logs; earlier builds may have completed."
        ) from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None

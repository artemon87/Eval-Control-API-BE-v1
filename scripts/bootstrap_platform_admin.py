import argparse
import asyncio
import os
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the initial EvalHub platform administrator assignment."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--principal-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--email")
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required safety acknowledgement before writing.",
    )
    return parser.parse_args()


async def bootstrap(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise RuntimeError("Refusing to write without --confirm")

    client = AsyncIOMotorClient(
        required_env("DS_DB_MONGODB_URI"),
        serverSelectionTimeoutMS=5_000,
    )
    database = client[required_env("DS_DB_MONGODB_NAME")]
    now = datetime.now(UTC)
    try:
        await client.admin.command("ping")
        await database.authorization_principals.update_one(
            {"tenant_id": args.tenant_id, "principal_id": args.principal_id},
            {
                "$set": {
                    "principal_type": "user",
                    "display_name": args.display_name,
                    "email": args.email,
                    "entra_roles_last_seen": ["EvalHub.Admin"],
                    "last_login_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"first_login_at": now},
            },
            upsert=True,
        )

        assignment = {
            "tenant_id": args.tenant_id,
            "principal_id": args.principal_id,
            "local_role": "platform_admin",
            "scope": {"type": "global", "id": "*"},
            "status": "active",
            "reason": args.reason,
            "granted_by": {
                "tenant_id": args.tenant_id,
                "principal_id": "bootstrap-script",
                "display_name": "Bootstrap script",
            },
            "created_at": now,
            "expires_at": None,
            "revoked_at": None,
            "revoked_by": None,
            "revocation_reason": None,
        }
        try:
            result = await database.authorization_assignments.insert_one(assignment)
        except DuplicateKeyError as exc:
            raise RuntimeError("An active platform_admin assignment already exists") from exc

        await database.authorization_audit_events.insert_one(
            {
                "event_type": "authorization.assignment.bootstrapped",
                "actor": assignment["granted_by"],
                "target": {
                    "tenant_id": args.tenant_id,
                    "principal_id": args.principal_id,
                    "display_name": args.display_name,
                },
                "assignment_id": str(result.inserted_id),
                "local_role": "platform_admin",
                "scope": assignment["scope"],
                "reason": args.reason,
                "occurred_at": now,
                "request_id": None,
            }
        )
        print(f"Created platform_admin assignment {result.inserted_id}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(bootstrap(parse_args()))

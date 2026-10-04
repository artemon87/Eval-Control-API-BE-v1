import asyncio
import os

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


async def main() -> None:
    uri = required_env("DS_DB_MONGODB_URI")
    database_name = required_env("DS_DB_MONGODB_NAME")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5_000)
    try:
        await client.admin.command("ping")
        database = client[database_name]

        await database.authorization_principals.create_indexes(
            [
                IndexModel(
                    [("tenant_id", ASCENDING), ("principal_id", ASCENDING)],
                    unique=True,
                    name="uq_authorization_principal",
                ),
                IndexModel(
                    [("tenant_id", ASCENDING), ("display_name", ASCENDING)],
                    name="ix_authorization_principal_name",
                ),
                IndexModel(
                    [("tenant_id", ASCENDING), ("last_login_at", DESCENDING)],
                    name="ix_authorization_principal_last_login",
                ),
            ]
        )

        await database.authorization_assignments.create_indexes(
            [
                IndexModel(
                    [
                        ("tenant_id", ASCENDING),
                        ("principal_id", ASCENDING),
                        ("local_role", ASCENDING),
                        ("scope.type", ASCENDING),
                        ("scope.id", ASCENDING),
                    ],
                    unique=True,
                    partialFilterExpression={"status": "active"},
                    name="uq_active_authorization_assignment",
                ),
                IndexModel(
                    [("tenant_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
                    name="ix_authorization_assignment_status_created",
                ),
                IndexModel(
                    [("status", ASCENDING), ("expires_at", ASCENDING)],
                    name="ix_authorization_assignment_expiration",
                ),
            ]
        )

        await database.authorization_audit_events.create_indexes(
            [
                IndexModel(
                    [("actor.tenant_id", ASCENDING), ("occurred_at", DESCENDING)],
                    name="ix_authorization_audit_tenant_time",
                ),
                IndexModel(
                    [("target.principal_id", ASCENDING), ("occurred_at", DESCENDING)],
                    name="ix_authorization_audit_target_time",
                ),
                IndexModel(
                    [("assignment_id", ASCENDING), ("occurred_at", DESCENDING)],
                    name="ix_authorization_audit_assignment_time",
                ),
            ]
        )

        await database.authorization_access_requests.create_indexes(
            [
                IndexModel(
                    [
                        ("tenant_id", ASCENDING),
                        ("principal_id", ASCENDING),
                        ("requested_role", ASCENDING),
                    ],
                    unique=True,
                    partialFilterExpression={
                        "status": {"$in": ["pending", "approved"]}
                    },
                    name="uq_open_authorization_access_request",
                ),
                IndexModel(
                    [
                        ("tenant_id", ASCENDING),
                        ("status", ASCENDING),
                        ("created_at", DESCENDING),
                    ],
                    name="ix_authorization_access_request_queue",
                ),
                IndexModel(
                    [
                        ("tenant_id", ASCENDING),
                        ("principal_id", ASCENDING),
                        ("created_at", DESCENDING),
                    ],
                    name="ix_authorization_access_request_requester",
                ),
            ]
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

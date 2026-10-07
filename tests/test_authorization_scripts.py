import argparse
import runpy
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pymongo
import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from scripts import bootstrap_platform_admin, create_authorization_indexes


class FakeCollection:
    def __init__(self) -> None:
        self.update_one = AsyncMock()
        self.insert_one = AsyncMock(
            return_value=SimpleNamespace(inserted_id=ObjectId())
        )
        self.create_indexes = AsyncMock()


class FakeDatabase:
    def __init__(self) -> None:
        self.authorization_principals = FakeCollection()
        self.authorization_assignments = FakeCollection()
        self.authorization_audit_events = FakeCollection()
        self.authorization_access_requests = FakeCollection()


class FakeClient:
    def __init__(self) -> None:
        self.database = FakeDatabase()
        self.admin = SimpleNamespace(command=AsyncMock())
        self.close = AsyncMock()
        self.requested_database: str | None = None

    def __getitem__(self, name: str) -> FakeDatabase:
        self.requested_database = name
        return self.database


@pytest.mark.parametrize(
    "module",
    [bootstrap_platform_admin, create_authorization_indexes],
)
def test_required_env_returns_value_or_raises(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_SETTING", "configured")
    assert module.required_env("TEST_SETTING") == "configured"
    monkeypatch.delenv("TEST_SETTING")
    with pytest.raises(RuntimeError, match="TEST_SETTING"):
        module.required_env("TEST_SETTING")


def test_bootstrap_parse_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap_platform_admin",
            "--tenant-id",
            "tenant-1",
            "--principal-id",
            "user-1",
            "--display-name",
            "Test User",
            "--email",
            "test@example.com",
            "--reason",
            "Initial platform administrator",
            "--confirm",
        ],
    )
    args = bootstrap_platform_admin.parse_args()
    assert args.tenant_id == "tenant-1"
    assert args.principal_id == "user-1"
    assert args.confirm is True


@pytest.mark.asyncio
async def test_bootstrap_requires_confirmation() -> None:
    with pytest.raises(RuntimeError, match="without --confirm"):
        await bootstrap_platform_admin.bootstrap(
            argparse.Namespace(confirm=False)
        )


@pytest.mark.asyncio
async def test_bootstrap_creates_principal_assignment_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    monkeypatch.setattr(
        bootstrap_platform_admin,
        "AsyncMongoClient",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setenv("DS_DB_MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("DS_DB_MONGODB_NAME", "evalhub")
    args = argparse.Namespace(
        confirm=True,
        tenant_id="tenant-1",
        principal_id="user-1",
        display_name="Test User",
        email="test@example.com",
        reason="Initial platform administrator",
    )

    await bootstrap_platform_admin.bootstrap(args)

    client.admin.command.assert_awaited_once_with("ping")
    client.database.authorization_principals.update_one.assert_awaited_once()
    client.database.authorization_assignments.insert_one.assert_awaited_once()
    client.database.authorization_audit_events.insert_one.assert_awaited_once()
    client.close.assert_awaited_once()
    assert "Created platform_admin assignment" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_bootstrap_translates_duplicate_and_always_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    client.database.authorization_assignments.insert_one.side_effect = DuplicateKeyError(
        "duplicate"
    )
    monkeypatch.setattr(
        bootstrap_platform_admin,
        "AsyncMongoClient",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setenv("DS_DB_MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("DS_DB_MONGODB_NAME", "evalhub")
    args = argparse.Namespace(
        confirm=True,
        tenant_id="tenant-1",
        principal_id="user-1",
        display_name="Test User",
        email=None,
        reason="Initial platform administrator",
    )

    with pytest.raises(RuntimeError, match="already exists"):
        await bootstrap_platform_admin.bootstrap(args)
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_indexes_builds_all_authorization_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    monkeypatch.setattr(
        create_authorization_indexes,
        "AsyncMongoClient",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setenv("DS_DB_MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("DS_DB_MONGODB_NAME", "evalhub")

    await create_authorization_indexes.main()

    assert client.requested_database == "evalhub"
    client.admin.command.assert_awaited_once_with("ping")
    client.close.assert_awaited_once()
    for collection in [
        client.database.authorization_principals,
        client.database.authorization_assignments,
        client.database.authorization_audit_events,
        client.database.authorization_access_requests,
    ]:
        collection.create_indexes.assert_awaited_once()
    request_indexes = (
        client.database.authorization_access_requests.create_indexes.await_args.args[0]
    )
    assert {index.document["name"] for index in request_indexes} == {
        "uq_open_authorization_access_request",
        "ix_authorization_access_request_queue",
        "ix_authorization_access_request_requester",
    }


@pytest.mark.asyncio
async def test_create_indexes_always_closes_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    client.admin.command.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(
        create_authorization_indexes,
        "AsyncMongoClient",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setenv("DS_DB_MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("DS_DB_MONGODB_NAME", "evalhub")

    with pytest.raises(RuntimeError, match="unavailable"):
        await create_authorization_indexes.main()
    client.close.assert_awaited_once()


def test_bootstrap_module_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    monkeypatch.setattr(pymongo, "AsyncMongoClient", lambda *_args, **_kwargs: client)
    monkeypatch.setenv("DS_DB_MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("DS_DB_MONGODB_NAME", "evalhub")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap_platform_admin",
            "--tenant-id",
            "tenant-1",
            "--principal-id",
            "user-1",
            "--display-name",
            "Test User",
            "--reason",
            "Initial platform administrator",
            "--confirm",
        ],
    )
    monkeypatch.delitem(sys.modules, "scripts.bootstrap_platform_admin", raising=False)
    runpy.run_module("scripts.bootstrap_platform_admin", run_name="__main__")
    client.close.assert_awaited_once()


def test_create_indexes_module_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    monkeypatch.setattr(pymongo, "AsyncMongoClient", lambda *_args, **_kwargs: client)
    monkeypatch.setenv("DS_DB_MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("DS_DB_MONGODB_NAME", "evalhub")
    monkeypatch.delitem(sys.modules, "scripts.create_authorization_indexes", raising=False)
    runpy.run_module("scripts.create_authorization_indexes", run_name="__main__")
    client.close.assert_awaited_once()

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWTError
from starlette.concurrency import run_in_threadpool

from src.models.auth import CurrentUser


logger = logging.getLogger(__name__)

SUPPORTED_ALGORITHMS = ["RS256"]

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthSettings:
    tenant_id: str
    audience: str
    expected_scope: str
    allowed_client_ids: frozenset[str]
    issuer: str
    jwks_uri: str
    clock_skew_seconds: int


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set"
        )

    return value


def _csv_env(name: str) -> frozenset[str]:
    raw_value = _required_env(name)

    values = frozenset(
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    )

    if not values:
        raise RuntimeError(
            f"Environment variable {name} must contain at least one value"
        )

    return values


def _integer_env(
    name: str,
    default: int,
    minimum: int = 0,
) -> int:
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            f"Environment variable {name} must be an integer"
        ) from error

    if value < minimum:
        raise RuntimeError(
            f"Environment variable {name} must be at least {minimum}"
        )

    return value


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    tenant_id = _required_env("ENTRA_TENANT_ID")

    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    jwks_uri = (
        f"https://login.microsoftonline.com/"
        f"{tenant_id}/discovery/v2.0/keys"
    )

    return AuthSettings(
        tenant_id=tenant_id,
        audience=_required_env("ENTRA_API_AUDIENCE"),
        expected_scope=_required_env("ENTRA_EXPECTED_SCOPE"),
        allowed_client_ids=_csv_env("ENTRA_ALLOWED_CLIENT_IDS"),
        issuer=issuer,
        jwks_uri=jwks_uri,
        clock_skew_seconds=_integer_env(
            "ENTRA_CLOCK_SKEW_SECONDS",
            default=60,
        ),
    )


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    settings = get_auth_settings()

    return PyJWKClient(
        settings.jwks_uri,
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=300,
        timeout=10,
    )


def validate_auth_configuration() -> None:
    """
    Call during application startup to fail fast when authentication
    configuration is missing or invalid.
    """
    get_auth_settings()
    get_jwks_client()


def anonymous_viewer() -> CurrentUser:
    return CurrentUser(
        authenticated=False,
        display_name="Anonymous",
    )


def _unauthorized(
    detail: str = "Invalid or expired access token",
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Identity provider signing keys are temporarily unavailable",
    )


def _required_string_claim(
    claims: dict[str, Any],
    name: str,
) -> str:
    value = claims.get(name)

    if not isinstance(value, str) or not value.strip():
        raise _unauthorized()

    return value.strip()


def _optional_string_claim(
    claims: dict[str, Any],
    name: str,
) -> str | None:
    value = claims.get(name)

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _scope_claim(claims: dict[str, Any]) -> frozenset[str]:
    raw_scopes = claims.get("scp")

    if not isinstance(raw_scopes, str):
        return frozenset()

    return frozenset(
        scope
        for scope in raw_scopes.split()
        if scope
    )


def _validate_authorized_client(
    claims: dict[str, Any],
    settings: AuthSettings,
) -> str:
    client_id = _required_string_claim(claims, "azp")

    if client_id not in settings.allowed_client_ids:
        logger.warning(
            "Rejected access token from unauthorized client application"
        )
        raise _unauthorized()

    return client_id


def _validate_scope(
    claims: dict[str, Any],
    settings: AuthSettings,
) -> frozenset[str]:
    scopes = _scope_claim(claims)

    if settings.expected_scope not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access token does not contain the required scope: "
                f"{settings.expected_scope}"
            ),
        )

    return scopes


async def authenticate_access_token(
    access_token: str,
) -> CurrentUser:
    settings = get_auth_settings()

    try:
        signing_key = await run_in_threadpool(
            get_jwks_client().get_signing_key_from_jwt,
            access_token,
        )

        claims: dict[str, Any] = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=SUPPORTED_ALGORITHMS,
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=settings.clock_skew_seconds,
            options={
                "require": [
                    "aud",
                    "exp",
                    "iat",
                    "iss",
                    "oid",
                    "sub",
                    "tid",
                    "ver",
                    "azp",
                    "scp",
                ],
            },
        )
    except PyJWKClientConnectionError as error:
        logger.exception(
            "Unable to retrieve Microsoft Entra signing keys"
        )
        raise _service_unavailable() from error
    except PyJWTError as error:
        logger.info(
            "Access token validation failed: %s: %s",
            error.__class__.__name__,
            error,
        )
        raise _unauthorized() from error

    tenant_id = _required_string_claim(claims, "tid")

    if tenant_id != settings.tenant_id:
        raise _unauthorized()

    token_version = _required_string_claim(claims, "ver")

    if token_version != "2.0":
        raise _unauthorized("Unsupported access token version")

    client_id = _validate_authorized_client(claims, settings)
    scopes = _validate_scope(claims, settings)

    object_id = _required_string_claim(claims, "oid")
    subject = _required_string_claim(claims, "sub")

    email = (
        _optional_string_claim(claims, "preferred_username")
        or _optional_string_claim(claims, "email")
        or _optional_string_claim(claims, "upn")
    )

    display_name = (
        _optional_string_claim(claims, "name")
        or email
        or object_id
    )

    return CurrentUser(
        authenticated=True,
        object_id=object_id,
        tenant_id=tenant_id,
        subject=subject,
        client_id=client_id,
        display_name=display_name,
        email=email,
        scopes=sorted(scopes),
    )


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> CurrentUser:
    authorization_header = request.headers.get("authorization")

    # No Authorization header means anonymous viewer access.
    if authorization_header is None:
        return anonymous_viewer()

    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise _unauthorized("Malformed Authorization header")

    return await authenticate_access_token(credentials.credentials)


def get_authenticated_user(
    current_user: CurrentUser = Depends(get_optional_user),
) -> CurrentUser:
    if not current_user.authenticated:
        raise _unauthorized("Authentication is required")

    return current_user
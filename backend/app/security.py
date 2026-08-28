"""Anonymous visitor identity and signed session-cookie helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import Header, HTTPException, Request, status
from starlette.datastructures import Headers, MutableHeaders


SESSION_COOKIE_NAME = "limituplab_visitor"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
MINIMUM_PRODUCTION_SECRET_LENGTH = 32
ADMIN_API_KEY_HEADER = "X-LimitUpLab-Admin-Key"
_LOCAL_DEVELOPMENT_SECRET = "limituplab-local-development-secret-only"
_OWNER_ID_PATTERN = re.compile(r"^visitor_[0-9a-f]{32}$")


@dataclass(frozen=True)
class VisitorIdentity:
    """One verified or newly generated anonymous visitor identity."""

    owner_id: str
    token: str
    is_new: bool


def is_production_environment() -> bool:
    """Return whether strict production security settings are required."""

    return os.getenv("LIMITUPLAB_ENVIRONMENT", "development").strip().lower() in {
        "production",
        "prod",
    }


def validate_session_security() -> None:
    """Fail fast when production uses a missing or weak signing secret."""

    if not is_production_environment():
        return
    secret = os.getenv("LIMITUPLAB_SESSION_SECRET", "")
    if len(secret.encode("utf-8")) < MINIMUM_PRODUCTION_SECRET_LENGTH:
        raise RuntimeError(
            "LIMITUPLAB_SESSION_SECRET must contain at least 32 bytes in production"
        )


def validate_admin_security() -> None:
    """Fail fast when production has no independent strong administrator key."""

    if not is_production_environment():
        return
    admin_key = os.getenv("LIMITUPLAB_ADMIN_KEY", "")
    if len(admin_key.encode("utf-8")) < MINIMUM_PRODUCTION_SECRET_LENGTH:
        raise RuntimeError(
            "LIMITUPLAB_ADMIN_KEY must contain at least 32 bytes in production"
        )
    session_secret = os.getenv("LIMITUPLAB_SESSION_SECRET", "")
    if session_secret and hmac.compare_digest(admin_key, session_secret):
        raise RuntimeError(
            "LIMITUPLAB_ADMIN_KEY must differ from LIMITUPLAB_SESSION_SECRET"
        )


def require_admin_access(
    provided_key: str | None = Header(default=None, alias=ADMIN_API_KEY_HEADER),
) -> None:
    """Authorize an internal endpoint without exposing the configured key."""

    configured_key = os.getenv("LIMITUPLAB_ADMIN_KEY", "")
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrator API is not configured.",
        )
    if provided_key is None or not hmac.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator authentication required.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def resolve_visitor_identity(cookie_token: str | None) -> VisitorIdentity:
    """Verify an existing cookie or create a fresh anonymous owner identity."""

    owner_id = verify_visitor_token(cookie_token)
    if owner_id is not None:
        return VisitorIdentity(owner_id=owner_id, token=cookie_token or "", is_new=False)
    owner_id = f"visitor_{uuid4().hex}"
    return VisitorIdentity(
        owner_id=owner_id,
        token=sign_visitor_owner_id(owner_id),
        is_new=True,
    )


def sign_visitor_owner_id(owner_id: str) -> str:
    """Create a tamper-evident token for one generated owner id."""

    if not _OWNER_ID_PATTERN.fullmatch(owner_id):
        raise ValueError("owner_id must be a generated visitor id")
    signature = hmac.new(
        _session_secret(),
        owner_id.encode("ascii"),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{owner_id}.{encoded_signature}"


def verify_visitor_token(token: str | None) -> str | None:
    """Return the signed owner id, or ``None`` for malformed/tampered tokens."""

    if not token or "." not in token:
        return None
    owner_id, supplied_signature = token.rsplit(".", 1)
    if not _OWNER_ID_PATTERN.fullmatch(owner_id):
        return None
    expected_token = sign_visitor_owner_id(owner_id)
    expected_signature = expected_token.rsplit(".", 1)[1]
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    return owner_id


def current_owner_id(request: Request) -> str:
    """Read the verified visitor identity installed by the ASGI middleware."""

    owner_id = getattr(request.state, "owner_id", None)
    if not isinstance(owner_id, str) or not _OWNER_ID_PATTERN.fullmatch(owner_id):
        raise HTTPException(status_code=500, detail="Visitor identity is unavailable.")
    return owner_id


class AnonymousVisitorMiddleware:
    """Install a signed anonymous owner id and refresh invalid visitor cookies."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        identity = resolve_visitor_identity(
            _cookie_value(request_headers.get("cookie", ""), SESSION_COOKIE_NAME)
        )
        scope.setdefault("state", {})["owner_id"] = identity.owner_id

        async def send_with_identity(message: dict[str, Any]) -> None:
            if identity.is_new and message.get("type") == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.append(
                    "set-cookie",
                    _session_cookie_header(identity.token),
                )
            await send(message)

        await self.app(scope, receive, send_with_identity)


def _session_secret() -> bytes:
    secret = os.getenv("LIMITUPLAB_SESSION_SECRET", "")
    if secret:
        return secret.encode("utf-8")
    if is_production_environment():
        validate_session_security()
    return _LOCAL_DEVELOPMENT_SECRET.encode("utf-8")


def _cookie_value(raw_cookie: str, name: str) -> str | None:
    if not raw_cookie:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw_cookie)
    except Exception:  # noqa: BLE001 - malformed client cookies are replaced.
        return None
    morsel = cookies.get(name)
    return morsel.value if morsel is not None else None


def _session_cookie_header(token: str) -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = token
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["max-age"] = str(SESSION_COOKIE_MAX_AGE)
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    if is_production_environment():
        morsel["secure"] = True
    return cookie.output(header="").strip()

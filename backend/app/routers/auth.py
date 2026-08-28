"""Public passwordless authentication endpoints."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.models import (
    AuthLoginResponse,
    AuthStatusResponse,
    AuthUser,
    EmailLoginChallengeResponse,
    EmailLoginRequest,
    EmailLoginVerifyRequest,
)
from app.repositories import (
    AuthChallengeError,
    IdentityConflictError,
    SQLiteAuthRepository,
)
from app.security import (
    AUTH_SESSION_COOKIE_NAME,
    clear_auth_session_cookie,
    current_user,
    current_visitor_owner_id,
    install_auth_session_cookie,
)
from app.services.auth_service import (
    EMAIL_CODE_TTL_SECONDS,
    AuthConfigurationError,
    AuthRateLimitError,
    AuthService,
    ExternalAuthError,
    email_login_enabled,
    frontend_url,
    github_login_enabled,
    normalize_return_path,
)


router = APIRouter()


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(request: Request) -> AuthStatusResponse:
    """Return the current user and configured login methods."""

    user = current_user(request)
    return AuthStatusResponse(
        authenticated=user is not None,
        user=user,
        email_login_enabled=email_login_enabled(),
        github_login_enabled=github_login_enabled(),
    )


@router.post("/email/request", response_model=EmailLoginChallengeResponse)
def request_email_login(
    payload: EmailLoginRequest,
    request: Request,
) -> EmailLoginChallengeResponse:
    """Send a short-lived email login code with per-address throttling."""

    user = current_user(request)
    try:
        result = AuthService().request_email_code(
            email=payload.email,
            visitor_owner_id=current_visitor_owner_id(request),
            link_user_id=user.user_id if user else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AuthRateLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
            headers={"Retry-After": "60"},
        ) from error
    except AuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ExternalAuthError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return EmailLoginChallengeResponse(
        challenge_id=result.challenge_id,
        expires_in_seconds=EMAIL_CODE_TTL_SECONDS,
        debug_code=result.debug_code,
    )


@router.post("/email/verify", response_model=AuthLoginResponse)
def verify_email_login(
    payload: EmailLoginVerifyRequest,
    request: Request,
    response: Response,
) -> AuthLoginResponse:
    """Verify one email code and install an opaque login session cookie."""

    try:
        result = AuthService().verify_email_code(
            challenge_id=payload.challenge_id,
            email=payload.email,
            code=payload.code,
            visitor_owner_id=current_visitor_owner_id(request),
        )
    except (ValueError, AuthChallengeError, IdentityConflictError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    install_auth_session_cookie(response, result.session_token)
    return AuthLoginResponse(
        user=result.user,
        migrated_chat_sessions=result.migrated_chat_sessions,
    )


@router.get("/github/start")
def start_github_login(
    request: Request,
    return_to: str = Query(default="/", max_length=300),
) -> RedirectResponse:
    """Redirect the browser to GitHub with state and PKCE protection."""

    existing_user = current_user(request)
    try:
        authorization_url = AuthService().start_github(
            visitor_owner_id=current_visitor_owner_id(request),
            link_user_id=existing_user.user_id if existing_user else None,
            return_to=return_to,
        )
    except AuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return RedirectResponse(authorization_url, status_code=303)


@router.get("/github/callback")
def finish_github_login(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Consume GitHub's callback and redirect back to the SPA."""

    if error or not code or not state:
        return _frontend_redirect("/", auth_error="github_denied")
    try:
        result, return_to = AuthService().finish_github(
            code=code,
            state=state,
            visitor_owner_id=current_visitor_owner_id(request),
        )
    except (
        AuthChallengeError,
        IdentityConflictError,
        ExternalAuthError,
        ValueError,
    ):
        return _frontend_redirect("/", auth_error="github_failed")
    response = _frontend_redirect(return_to, auth="success")
    install_auth_session_cookie(response, result.session_token)
    return response


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    """Revoke the current server-side session and clear its cookie."""

    SQLiteAuthRepository().revoke_session(
        request.cookies.get(AUTH_SESSION_COOKIE_NAME)
    )
    clear_auth_session_cookie(response)
    return {"logged_out": True}


def _frontend_redirect(path: str, **query: str) -> RedirectResponse:
    safe_path = normalize_return_path(path)
    separator = "&" if "?" in safe_path else "?"
    target = f"{frontend_url()}{safe_path}"
    if query:
        target = f"{target}{separator}{urlencode(query)}"
    return RedirectResponse(target, status_code=303)

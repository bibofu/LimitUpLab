"""Passwordless email and GitHub OAuth authentication orchestration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from app.models import AuthUser
from app.repositories import SQLiteAuthRepository
from app.security import AUTH_SESSION_MAX_AGE, is_production_environment


EMAIL_CODE_TTL_SECONDS = 10 * 60
OAUTH_STATE_TTL_SECONDS = 10 * 60
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AuthConfigurationError(RuntimeError):
    """Raised when a requested authentication method is not configured."""


class AuthRateLimitError(RuntimeError):
    """Raised when email verification requests exceed a safe rate."""


class ExternalAuthError(RuntimeError):
    """Raised when an external identity provider rejects a login."""


@dataclass(frozen=True)
class EmailChallengeResult:
    challenge_id: str
    debug_code: str | None


@dataclass(frozen=True)
class LoginResult:
    user: AuthUser
    session_token: str
    migrated_chat_sessions: int


class SMTPEmailSender:
    """Deliver one short-lived login code through configured SMTP."""

    def send_login_code(self, email: str, code: str) -> None:
        host = os.getenv("LIMITUPLAB_SMTP_HOST", "").strip()
        username = os.getenv("LIMITUPLAB_SMTP_USERNAME", "").strip()
        password = os.getenv("LIMITUPLAB_SMTP_PASSWORD", "")
        from_email = os.getenv("LIMITUPLAB_SMTP_FROM_EMAIL", "").strip()
        port = int(os.getenv("LIMITUPLAB_SMTP_PORT", "587"))
        if not host or not from_email:
            raise AuthConfigurationError("邮件发送服务尚未配置")

        message = EmailMessage()
        message["Subject"] = "LimitUpLab 登录验证码"
        message["From"] = from_email
        message["To"] = email
        message.set_content(
            f"你的 LimitUpLab 登录验证码是：{code}\n\n"
            f"验证码将在 {EMAIL_CODE_TTL_SECONDS // 60} 分钟后失效。"
            "如果不是你本人操作，请忽略这封邮件。"
        )

        use_ssl = os.getenv("LIMITUPLAB_SMTP_SSL", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        try:
            with smtp_class(host, port, timeout=10) as client:
                if not use_ssl and os.getenv(
                    "LIMITUPLAB_SMTP_STARTTLS", "true"
                ).strip().lower() in {"1", "true", "yes", "on"}:
                    client.starttls()
                if username:
                    client.login(username, password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise ExternalAuthError("验证码邮件发送失败，请稍后再试") from error


class GitHubOAuthClient:
    """Exchange a GitHub authorization code and read the stable user profile."""

    def fetch_profile(self, code: str, code_verifier: str) -> dict[str, Any]:
        try:
            token_response = requests.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": github_client_id(),
                    "client_secret": github_client_secret(),
                    "code": code,
                    "redirect_uri": github_callback_url(),
                    "code_verifier": code_verifier,
                },
                timeout=15,
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = str(token_payload.get("access_token") or "")
            if not access_token:
                raise ExternalAuthError("GitHub 未返回有效访问令牌")

            profile_response = requests.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15,
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
        except (requests.RequestException, ValueError) as error:
            raise ExternalAuthError("GitHub 登录服务暂时不可用") from error
        if profile.get("id") is None or not profile.get("login"):
            raise ExternalAuthError("GitHub 用户资料不完整")
        return profile


class AuthService:
    """Coordinate passwordless challenges, identities and login sessions."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        email_sender: SMTPEmailSender | None = None,
        github_client: GitHubOAuthClient | None = None,
    ) -> None:
        self.repository = SQLiteAuthRepository(database_path)
        self.email_sender = email_sender or SMTPEmailSender()
        self.github_client = github_client or GitHubOAuthClient()

    def request_email_code(
        self,
        *,
        email: str,
        visitor_owner_id: str,
        link_user_id: str | None = None,
    ) -> EmailChallengeResult:
        """Create and deliver a rate-limited six-digit email code."""

        if not email_login_enabled():
            raise AuthConfigurationError("邮箱验证码登录尚未启用")
        normalized = normalize_email(email)
        now = datetime.now(timezone.utc)
        if self.repository.recent_challenge_count(
            challenge_type="email",
            subject=normalized,
            since=now - timedelta(minutes=1),
        ) >= 1:
            raise AuthRateLimitError("验证码发送过于频繁，请稍后再试")
        if self.repository.recent_challenge_count(
            challenge_type="email",
            subject=normalized,
            since=now - timedelta(hours=1),
        ) >= 5:
            raise AuthRateLimitError("该邮箱请求次数过多，请一小时后再试")

        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge_id = self.repository.create_challenge(
            challenge_type="email",
            subject=normalized,
            secret_hash=_challenge_hash("email", normalized, code),
            visitor_owner_id=visitor_owner_id,
            link_user_id=link_user_id,
            expires_at=now + timedelta(seconds=EMAIL_CODE_TTL_SECONDS),
        )
        if email_delivery_mode() == "smtp":
            self.email_sender.send_login_code(normalized, code)
            debug_code = None
        else:
            debug_code = code
        return EmailChallengeResult(challenge_id=challenge_id, debug_code=debug_code)

    def verify_email_code(
        self,
        *,
        challenge_id: str,
        email: str,
        code: str,
        visitor_owner_id: str,
    ) -> LoginResult:
        """Consume an email code and issue one server-side login session."""

        normalized = normalize_email(email)
        challenge = self.repository.consume_challenge(
            challenge_id=challenge_id,
            challenge_type="email",
            subject=normalized,
            secret_hash=_challenge_hash("email", normalized, code),
            visitor_owner_id=visitor_owner_id,
        )
        user = self.repository.authenticate_identity(
            provider="email",
            provider_user_id=normalized,
            display_name=normalized.split("@", 1)[0],
            email=normalized,
            email_verified=True,
            link_user_id=challenge.get("link_user_id"),
        )
        return self._finish_login(user, visitor_owner_id)

    def start_github(
        self,
        *,
        visitor_owner_id: str,
        link_user_id: str | None = None,
        return_to: str = "/",
    ) -> str:
        """Create a state + PKCE challenge and return GitHub's authorization URL."""

        if not github_login_enabled():
            raise AuthConfigurationError("GitHub 登录尚未配置")
        state_secret = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        code_challenge = _base64url(hashlib.sha256(code_verifier.encode()).digest())
        safe_return_to = normalize_return_path(return_to)
        challenge_id = self.repository.create_challenge(
            challenge_type="github",
            subject="github",
            secret_hash=_challenge_hash("github", "github", state_secret),
            visitor_owner_id=visitor_owner_id,
            link_user_id=link_user_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
            metadata={
                "code_verifier": code_verifier,
                "return_to": safe_return_to,
            },
        )
        state = f"{challenge_id}.{state_secret}"
        return "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": github_client_id(),
                "redirect_uri": github_callback_url(),
                "scope": "read:user",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )

    def finish_github(
        self,
        *,
        code: str,
        state: str,
        visitor_owner_id: str,
    ) -> tuple[LoginResult, str]:
        """Validate GitHub callback state, fetch identity and issue a session."""

        try:
            challenge_id, state_secret = state.split(".", 1)
        except ValueError as error:
            raise ExternalAuthError("GitHub 登录状态无效") from error
        challenge = self.repository.consume_challenge(
            challenge_id=challenge_id,
            challenge_type="github",
            subject="github",
            secret_hash=_challenge_hash("github", "github", state_secret),
            visitor_owner_id=visitor_owner_id,
        )
        metadata = challenge.get("metadata") or {}
        profile = self.github_client.fetch_profile(
            code,
            str(metadata.get("code_verifier") or ""),
        )
        login = str(profile["login"])
        user = self.repository.authenticate_identity(
            provider="github",
            provider_user_id=str(profile["id"]),
            display_name=str(profile.get("name") or login),
            username=login,
            email=str(profile["email"]) if profile.get("email") else None,
            email_verified=False,
            avatar_url=str(profile["avatar_url"]) if profile.get("avatar_url") else None,
            link_user_id=challenge.get("link_user_id"),
        )
        return self._finish_login(user, visitor_owner_id), normalize_return_path(
            str(metadata.get("return_to") or "/")
        )

    def _finish_login(self, user: AuthUser, visitor_owner_id: str) -> LoginResult:
        token = secrets.token_urlsafe(48)
        self.repository.create_session(
            user.user_id,
            raw_token=token,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=AUTH_SESSION_MAX_AGE),
        )
        migrated = self.repository.migrate_chat_sessions(
            visitor_owner_id,
            user.user_id,
        )
        return LoginResult(
            user=user,
            session_token=token,
            migrated_chat_sessions=migrated,
        )


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("请输入有效的邮箱地址")
    return normalized


def normalize_return_path(value: str) -> str:
    normalized = value.strip() or "/"
    if (
        not normalized.startswith("/")
        or normalized.startswith("//")
        or any(character in normalized for character in ("\r", "\n", "\0"))
    ):
        return "/"
    return normalized


def email_login_enabled() -> bool:
    configured = os.getenv("LIMITUPLAB_EMAIL_LOGIN_ENABLED")
    if configured is not None:
        enabled = configured.strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled = not is_production_environment()
    return enabled and (email_delivery_mode() == "smtp" or not is_production_environment())


def email_delivery_mode() -> str:
    configured = os.getenv("LIMITUPLAB_EMAIL_DELIVERY_MODE", "").strip().lower()
    if configured in {"smtp", "debug"}:
        return configured
    return "smtp" if os.getenv("LIMITUPLAB_SMTP_HOST", "").strip() else "debug"


def github_login_enabled() -> bool:
    return bool(github_client_id() and github_client_secret())


def validate_auth_configuration() -> None:
    """Fail fast for incomplete production authentication settings."""

    if not is_production_environment():
        return
    email_requested = os.getenv(
        "LIMITUPLAB_EMAIL_LOGIN_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if email_requested:
        if email_delivery_mode() != "smtp":
            raise RuntimeError("Production email login requires SMTP delivery mode")
        if not os.getenv("LIMITUPLAB_SMTP_HOST", "").strip() or not os.getenv(
            "LIMITUPLAB_SMTP_FROM_EMAIL", ""
        ).strip():
            raise RuntimeError("Production email login requires SMTP host and sender")

    client_id = github_client_id()
    client_secret = github_client_secret()
    if bool(client_id) != bool(client_secret):
        raise RuntimeError("GitHub OAuth client id and secret must be configured together")
    if client_id and not github_callback_url().startswith("https://"):
        raise RuntimeError("Production GitHub OAuth callback must use HTTPS")
    if (email_requested or client_id) and not frontend_url().startswith("https://"):
        raise RuntimeError("Production authentication frontend URL must use HTTPS")

    login_required = os.getenv(
        "LIMITUPLAB_AGENT_LOGIN_REQUIRED", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if login_required and not (email_login_enabled() or github_login_enabled()):
        raise RuntimeError(
            "At least one login method is required when Agent login is enforced"
        )


def github_client_id() -> str:
    return os.getenv("LIMITUPLAB_GITHUB_CLIENT_ID", "").strip()


def github_client_secret() -> str:
    return os.getenv("LIMITUPLAB_GITHUB_CLIENT_SECRET", "").strip()


def github_callback_url() -> str:
    return os.getenv(
        "LIMITUPLAB_GITHUB_CALLBACK_URL",
        "http://127.0.0.1:8001/api/auth/github/callback",
    ).strip()


def frontend_url() -> str:
    return os.getenv("LIMITUPLAB_FRONTEND_URL", "http://127.0.0.1:5173").strip().rstrip("/")


def _challenge_hash(challenge_type: str, subject: str, secret: str) -> str:
    signing_secret = os.getenv(
        "LIMITUPLAB_SESSION_SECRET",
        "limituplab-local-development-secret-only",
    ).encode("utf-8")
    payload = f"{challenge_type}\0{subject}\0{secret}".encode("utf-8")
    return hmac.new(signing_secret, payload, hashlib.sha256).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

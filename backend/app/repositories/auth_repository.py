"""SQLite persistence for users, identities, login sessions and challenges."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.database import connect, initialize_database
from app.models import AuthUser


class AuthChallengeError(ValueError):
    """Raised when an authentication challenge is invalid or expired."""


class IdentityConflictError(ValueError):
    """Raised when an external identity already belongs to another user."""


class SQLiteAuthRepository:
    """Persist authentication state without storing raw login secrets."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def create_challenge(
        self,
        *,
        challenge_type: str,
        subject: str,
        secret_hash: str,
        visitor_owner_id: str,
        expires_at: datetime,
        link_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create one single-use authentication challenge."""

        challenge_id = f"challenge_{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO auth_challenges (
                    challenge_id, challenge_type, subject, secret_hash,
                    visitor_owner_id, link_user_id, metadata_json,
                    attempt_count, created_at, expires_at, consumed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)
                """,
                (
                    challenge_id,
                    challenge_type,
                    subject,
                    secret_hash,
                    visitor_owner_id,
                    link_user_id,
                    json.dumps(metadata or {}, ensure_ascii=True),
                    now,
                    expires_at.isoformat(),
                ),
            )
            connection.commit()
            return challenge_id
        finally:
            connection.close()

    def recent_challenge_count(
        self,
        *,
        challenge_type: str,
        subject: str,
        since: datetime,
    ) -> int:
        """Count recently created challenges for abuse control."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM auth_challenges
                WHERE challenge_type = ? AND subject = ? AND created_at >= ?
                """,
                (challenge_type, subject, since.isoformat()),
            ).fetchone()
            return int(row["count"] or 0)
        finally:
            connection.close()

    def consume_challenge(
        self,
        *,
        challenge_id: str,
        challenge_type: str,
        subject: str,
        secret_hash: str,
        visitor_owner_id: str,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        """Validate and atomically consume one challenge."""

        now = datetime.now(timezone.utc)
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM auth_challenges WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
            if row is None or row["challenge_type"] != challenge_type:
                raise AuthChallengeError("认证请求不存在或已失效")
            if row["subject"] != subject or row["visitor_owner_id"] != visitor_owner_id:
                raise AuthChallengeError("认证请求与当前会话不匹配")
            if row["consumed_at"] is not None:
                raise AuthChallengeError("认证请求已经使用")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise AuthChallengeError("验证码已经过期")
            attempts = int(row["attempt_count"] or 0)
            if attempts >= max_attempts:
                raise AuthChallengeError("验证码尝试次数过多")
            if not hmac.compare_digest(str(row["secret_hash"]), secret_hash):
                connection.execute(
                    """
                    UPDATE auth_challenges
                    SET attempt_count = attempt_count + 1
                    WHERE challenge_id = ?
                    """,
                    (challenge_id,),
                )
                connection.commit()
                raise AuthChallengeError("验证码不正确")
            connection.execute(
                "UPDATE auth_challenges SET consumed_at = ? WHERE challenge_id = ?",
                (now.isoformat(), challenge_id),
            )
            connection.commit()
            return {
                "link_user_id": row["link_user_id"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def authenticate_identity(
        self,
        *,
        provider: str,
        provider_user_id: str,
        display_name: str,
        username: str | None = None,
        email: str | None = None,
        email_verified: bool = False,
        avatar_url: str | None = None,
        link_user_id: str | None = None,
    ) -> AuthUser:
        """Find, create or explicitly link one external login identity."""

        now = datetime.now(timezone.utc).isoformat()
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute(
                """
                SELECT user_id FROM user_identities
                WHERE provider = ? AND provider_user_id = ?
                """,
                (provider, provider_user_id),
            ).fetchone()
            if identity is not None:
                user_id = str(identity["user_id"])
                if link_user_id is not None and user_id != link_user_id:
                    raise IdentityConflictError("该登录方式已绑定其他账号")
            elif link_user_id is not None:
                user_id = link_user_id
                if connection.execute(
                    "SELECT 1 FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone() is None:
                    raise IdentityConflictError("待绑定账号不存在")
            else:
                user_id = f"user_{uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, display_name, avatar_url, primary_email,
                        status, created_at, updated_at, last_login_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        user_id,
                        display_name,
                        avatar_url,
                        email if email_verified else None,
                        now,
                        now,
                        now,
                    ),
                )

            linking_new_identity = identity is None and link_user_id is not None

            connection.execute(
                """
                INSERT INTO user_identities (
                    provider, provider_user_id, user_id, username, email,
                    email_verified, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_user_id) DO UPDATE SET
                    username = excluded.username,
                    email = excluded.email,
                    email_verified = excluded.email_verified,
                    updated_at = excluded.updated_at
                """,
                (
                    provider,
                    provider_user_id,
                    user_id,
                    username,
                    email,
                    int(email_verified),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE users
                SET display_name = CASE WHEN ? = 1 THEN display_name ELSE ? END,
                    avatar_url = CASE
                        WHEN ? = 1 THEN avatar_url
                        ELSE COALESCE(?, avatar_url)
                    END,
                    primary_email = CASE
                        WHEN ? = 1 THEN COALESCE(primary_email, ?)
                        ELSE primary_email
                    END,
                    updated_at = ?,
                    last_login_at = ?
                WHERE user_id = ?
                """,
                (
                    int(linking_new_identity),
                    display_name,
                    int(linking_new_identity),
                    avatar_url,
                    int(email_verified),
                    email,
                    now,
                    now,
                    user_id,
                ),
            )
            connection.commit()
            user = self._get_user_with_connection(connection, user_id)
            if user is None:
                raise RuntimeError("登录用户创建失败")
            return user
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_session(self, user_id: str, *, raw_token: str, expires_at: datetime) -> None:
        """Persist a hash of one opaque browser session token."""

        now = datetime.now(timezone.utc).isoformat()
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    session_id, user_id, token_hash, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    f"session_{uuid4().hex}",
                    user_id,
                    _token_hash(raw_token),
                    now,
                    expires_at.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def resolve_session(self, raw_token: str | None) -> AuthUser | None:
        """Resolve one unexpired, non-revoked browser session."""

        if not raw_token:
            return None
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT user_id FROM auth_sessions
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (_token_hash(raw_token), datetime.now(timezone.utc).isoformat()),
            ).fetchone()
            if row is None:
                return None
            return self._get_user_with_connection(connection, str(row["user_id"]))
        finally:
            connection.close()

    def revoke_session(self, raw_token: str | None) -> None:
        """Revoke one browser session without revealing whether it existed."""

        if not raw_token:
            return
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (datetime.now(timezone.utc).isoformat(), _token_hash(raw_token)),
            )
            connection.commit()
        finally:
            connection.close()

    def migrate_chat_sessions(self, visitor_owner_id: str, user_id: str) -> int:
        """Move anonymous conversations to a newly authenticated user."""

        if visitor_owner_id == user_id:
            return 0
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            cursor = connection.execute(
                "UPDATE chat_sessions SET owner_id = ? WHERE owner_id = ?",
                (user_id, visitor_owner_id),
            )
            connection.commit()
            return max(0, int(cursor.rowcount))
        finally:
            connection.close()

    def _get_user_with_connection(
        self,
        connection: sqlite3.Connection,
        user_id: str,
    ) -> AuthUser | None:
        row = connection.execute(
            "SELECT * FROM users WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        providers = [
            str(item["provider"])
            for item in connection.execute(
                """
                SELECT provider FROM user_identities
                WHERE user_id = ? ORDER BY provider
                """,
                (user_id,),
            ).fetchall()
        ]
        return AuthUser(
            user_id=user_id,
            display_name=row["display_name"],
            avatar_url=row["avatar_url"],
            email=row["primary_email"],
            providers=providers,
            created_at=datetime.fromisoformat(row["created_at"]),
        )


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

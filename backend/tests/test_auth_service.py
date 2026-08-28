import os
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from unittest.mock import patch

from fastapi import Request, Response

from app.models import (
    ChatSessionCreateRequest,
    EmailLoginRequest,
    EmailLoginVerifyRequest,
)
from app.repositories import AuthChallengeError, SQLiteAuthRepository
from app.routers.agents import create_chat_session, list_chat_sessions
from app.routers.auth import (
    auth_status,
    logout,
    request_email_login,
    verify_email_login,
)
from app.security import AUTH_SESSION_COOKIE_NAME
from app.services.auth_service import AuthService, validate_auth_configuration


class FakeGitHubClient:
    def __init__(self) -> None:
        self.code = ""
        self.code_verifier = ""

    def fetch_profile(self, code: str, code_verifier: str):
        self.code = code
        self.code_verifier = code_verifier
        return {
            "id": 123456,
            "login": "limitup-user",
            "name": "LimitUp User",
            "avatar_url": "https://avatars.example/user.png",
            "email": None,
        }


class AuthServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f".test_auth_{uuid4().hex}.sqlite"
        )
        self.addCleanup(self.database_path.unlink, missing_ok=True)
        self.environment = patch.dict(
            os.environ,
            {
                "LIMITUPLAB_DATABASE_PATH": str(self.database_path),
                "LIMITUPLAB_ENVIRONMENT": "test",
                "LIMITUPLAB_SESSION_SECRET": "test-auth-secret-that-is-long-enough",
                "LIMITUPLAB_EMAIL_LOGIN_ENABLED": "true",
                "LIMITUPLAB_EMAIL_DELIVERY_MODE": "debug",
                "LIMITUPLAB_GITHUB_CLIENT_ID": "github-client-id",
                "LIMITUPLAB_GITHUB_CLIENT_SECRET": "github-client-secret",
                "LIMITUPLAB_GITHUB_CALLBACK_URL": (
                    "http://127.0.0.1:8001/api/auth/github/callback"
                ),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_email_login_migrates_anonymous_chats_and_stores_hashed_session(self) -> None:
        visitor = "visitor_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        created = create_chat_session(ChatSessionCreateRequest(), owner_id=visitor)
        service = AuthService(self.database_path)

        challenge = service.request_email_code(
            email="User@Example.com",
            visitor_owner_id=visitor,
        )
        self.assertRegex(challenge.debug_code or "", r"^\d{6}$")
        result = service.verify_email_code(
            challenge_id=challenge.challenge_id,
            email="user@example.com",
            code=challenge.debug_code or "",
            visitor_owner_id=visitor,
        )

        self.assertEqual(result.user.email, "user@example.com")
        self.assertEqual(result.user.providers, ["email"])
        self.assertEqual(result.migrated_chat_sessions, 1)
        self.assertEqual(
            list_chat_sessions(owner_id=result.user.user_id, limit=30).sessions[0].session_id,
            created.session_id,
        )
        resolved = SQLiteAuthRepository(self.database_path).resolve_session(
            result.session_token
        )
        self.assertEqual(resolved.user_id if resolved else None, result.user.user_id)

        with self.database_path.open("rb") as database_file:
            database_bytes = database_file.read()
        self.assertNotIn(result.session_token.encode(), database_bytes)
        self.assertNotIn((challenge.debug_code or "").encode(), database_bytes)

    def test_email_challenge_cannot_be_used_by_another_visitor(self) -> None:
        service = AuthService(self.database_path)
        challenge = service.request_email_code(
            email="user@example.com",
            visitor_owner_id="visitor_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

        with self.assertRaises(AuthChallengeError):
            service.verify_email_code(
                challenge_id=challenge.challenge_id,
                email="user@example.com",
                code=challenge.debug_code or "",
                visitor_owner_id="visitor_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            )

    def test_github_login_uses_state_pkce_and_stable_numeric_identity(self) -> None:
        visitor = "visitor_cccccccccccccccccccccccccccccccc"
        github_client = FakeGitHubClient()
        service = AuthService(
            self.database_path,
            github_client=github_client,
        )

        authorization_url = service.start_github(
            visitor_owner_id=visitor,
            return_to="/review",
        )
        query = parse_qs(urlparse(authorization_url).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(query["code_challenge"][0])

        result, return_to = service.finish_github(
            code="temporary-code",
            state=query["state"][0],
            visitor_owner_id=visitor,
        )

        self.assertEqual(return_to, "/review")
        self.assertEqual(result.user.display_name, "LimitUp User")
        self.assertEqual(result.user.providers, ["github"])
        self.assertEqual(github_client.code, "temporary-code")
        self.assertTrue(github_client.code_verifier)

        second_url = service.start_github(visitor_owner_id=visitor)
        second_state = parse_qs(urlparse(second_url).query)["state"][0]
        with self.assertRaises(AuthChallengeError):
            service.finish_github(
                code="temporary-code",
                state=second_state,
                visitor_owner_id="visitor_dddddddddddddddddddddddddddddddd",
            )

    def test_verified_email_can_link_to_existing_github_user(self) -> None:
        visitor = "visitor_ffffffffffffffffffffffffffffffff"
        service = AuthService(
            self.database_path,
            github_client=FakeGitHubClient(),
        )
        github_url = service.start_github(visitor_owner_id=visitor)
        github_state = parse_qs(urlparse(github_url).query)["state"][0]
        github_result, _ = service.finish_github(
            code="github-link-code",
            state=github_state,
            visitor_owner_id=visitor,
        )

        email_challenge = service.request_email_code(
            email="linked@example.com",
            visitor_owner_id=visitor,
            link_user_id=github_result.user.user_id,
        )
        linked_result = service.verify_email_code(
            challenge_id=email_challenge.challenge_id,
            email="linked@example.com",
            code=email_challenge.debug_code or "",
            visitor_owner_id=visitor,
        )

        self.assertEqual(linked_result.user.user_id, github_result.user.user_id)
        self.assertEqual(linked_result.user.display_name, "LimitUp User")
        self.assertEqual(linked_result.user.email, "linked@example.com")
        self.assertEqual(linked_result.user.providers, ["email", "github"])

    def test_auth_http_flow_sets_and_revokes_http_only_session(self) -> None:
        visitor = "visitor_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        anonymous_session = create_chat_session(
            ChatSessionCreateRequest(title="登录前会话"),
            owner_id=visitor,
        )
        self.assertTrue(anonymous_session.session_id)
        anonymous_request = _request(visitor)

        challenge = request_email_login(
            EmailLoginRequest(email="http-user@example.com"),
            anonymous_request,
        )
        self.assertIsNotNone(challenge.debug_code)
        login_response = Response()
        verified = verify_email_login(
            EmailLoginVerifyRequest(
                challenge_id=challenge.challenge_id,
                email="http-user@example.com",
                code=challenge.debug_code or "",
            ),
            anonymous_request,
            login_response,
        )
        self.assertEqual(verified.migrated_chat_sessions, 1)
        set_cookie = login_response.headers["set-cookie"]
        self.assertIn("HttpOnly", set_cookie)
        cookies = SimpleCookie()
        cookies.load(set_cookie)
        auth_token = cookies[AUTH_SESSION_COOKIE_NAME].value

        authenticated_request = _request(
            visitor,
            cookie=f"{AUTH_SESSION_COOKIE_NAME}={auth_token}",
        )
        status_response = auth_status(authenticated_request)
        self.assertTrue(status_response.authenticated)
        self.assertEqual(
            status_response.user.email if status_response.user else None,
            "http-user@example.com",
        )

        logout_response = Response()
        self.assertEqual(
            logout(authenticated_request, logout_response),
            {"logged_out": True},
        )
        self.assertFalse(auth_status(authenticated_request).authenticated)

    def test_production_rejects_required_login_without_any_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIMITUPLAB_ENVIRONMENT": "production",
                "LIMITUPLAB_AGENT_LOGIN_REQUIRED": "true",
                "LIMITUPLAB_EMAIL_LOGIN_ENABLED": "false",
                "LIMITUPLAB_GITHUB_CLIENT_ID": "",
                "LIMITUPLAB_GITHUB_CLIENT_SECRET": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                validate_auth_configuration()

def _request(visitor_owner_id: str, *, cookie: str = "") -> Request:
    headers = [(b"cookie", cookie.encode("ascii"))] if cookie else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "state": {
                "owner_id": visitor_owner_id,
                "visitor_owner_id": visitor_owner_id,
            },
        }
    )


if __name__ == "__main__":
    unittest.main()

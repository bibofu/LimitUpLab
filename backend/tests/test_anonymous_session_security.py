import asyncio
import os
import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.agents import router as agents_router
from app.security import (
    ADMIN_API_KEY_HEADER,
    AnonymousVisitorMiddleware,
    SESSION_COOKIE_NAME,
    require_admin_access,
    resolve_visitor_identity,
    sign_visitor_owner_id,
    validate_admin_security,
    validate_session_security,
    verify_visitor_token,
)


class AnonymousSessionSecurityTest(unittest.TestCase):
    def test_admin_access_rejects_missing_and_invalid_keys(self) -> None:
        with patch.dict(
            os.environ,
            {"LIMITUPLAB_ADMIN_KEY": ""},
            clear=False,
        ):
            with self.assertRaises(HTTPException) as unconfigured:
                require_admin_access(None)
        self.assertEqual(unconfigured.exception.status_code, 503)

        with patch.dict(
            os.environ,
            {"LIMITUPLAB_ADMIN_KEY": "admin-key-that-is-long-enough-for-tests"},
            clear=False,
        ):
            with self.assertRaises(HTTPException) as unauthorized:
                require_admin_access("wrong-key")
            self.assertIsNone(
                require_admin_access("admin-key-that-is-long-enough-for-tests")
            )
        self.assertEqual(unauthorized.exception.status_code, 401)
        self.assertEqual(
            unauthorized.exception.headers["WWW-Authenticate"],
            "ApiKey",
        )

    def test_production_admin_key_must_be_strong_and_independent(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIMITUPLAB_ENVIRONMENT": "production",
                "LIMITUPLAB_SESSION_SECRET": "same-secret-value-that-is-long-enough",
                "LIMITUPLAB_ADMIN_KEY": "same-secret-value-that-is-long-enough",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                validate_admin_security()

    def test_sensitive_routes_require_the_admin_dependency(self) -> None:
        protected_paths = {
            "/scoring-policies",
            "/scoring-policies/optimize",
            "/data-health",
            "/system-health",
            "/daily-pipeline-status",
            "/eval",
            "/prediction-quality-audit",
            "/factor-signal-diagnostic",
            "/runs",
            "/usage",
        }
        routes = {
            getattr(route, "path", ""): route for route in agents_router.routes
        }

        for path in protected_paths:
            route = routes[path]
            dependency_calls = {
                dependency.call for dependency in route.dependant.dependencies
            }
            self.assertIn(require_admin_access, dependency_calls, path)

        for public_path in {
            "/first-board-ratings",
            "/rating-backtest",
            "/rating-evaluation",
            "/review-report",
            "/chat/stream",
        }:
            route = routes[public_path]
            dependency_calls = {
                dependency.call for dependency in route.dependant.dependencies
            }
            self.assertNotIn(require_admin_access, dependency_calls, public_path)

        self.assertEqual(ADMIN_API_KEY_HEADER, "X-LimitUpLab-Admin-Key")

    def test_signed_owner_token_rejects_tampering(self) -> None:
        owner_id = "visitor_0123456789abcdef0123456789abcdef"
        with patch.dict(
            os.environ,
            {
                "LIMITUPLAB_ENVIRONMENT": "test",
                "LIMITUPLAB_SESSION_SECRET": "s" * 32,
            },
            clear=False,
        ):
            token = sign_visitor_owner_id(owner_id)

            self.assertEqual(verify_visitor_token(token), owner_id)
            self.assertIsNone(verify_visitor_token(f"{token}x"))
            self.assertFalse(resolve_visitor_identity(f"{token}x").owner_id == owner_id)

    def test_production_requires_a_strong_session_secret(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIMITUPLAB_ENVIRONMENT": "production",
                "LIMITUPLAB_SESSION_SECRET": "too-short",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                validate_session_security()

    def test_middleware_sets_http_only_cookie_and_reuses_identity(self) -> None:
        captured_owner_ids: list[str] = []

        async def downstream(scope, _receive, send) -> None:
            captured_owner_ids.append(scope["state"]["owner_id"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def request(cookie_header: str = "") -> list[dict]:
            sent: list[dict] = []

            async def receive() -> dict:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict) -> None:
                sent.append(message)

            headers = []
            if cookie_header:
                headers.append((b"cookie", cookie_header.encode("ascii")))
            await AnonymousVisitorMiddleware(downstream)(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/agents/chat/sessions",
                    "headers": headers,
                    "state": {},
                },
                receive,
                send,
            )
            return sent

        with patch.dict(
            os.environ,
            {
                "LIMITUPLAB_ENVIRONMENT": "test",
                "LIMITUPLAB_SESSION_SECRET": "test-session-secret-that-is-long-enough",
            },
            clear=False,
        ):
            first_messages = asyncio.run(request())
            response_headers = dict(first_messages[0]["headers"])
            set_cookie = response_headers[b"set-cookie"].decode("latin-1")
            cookies = SimpleCookie()
            cookies.load(set_cookie)
            token = cookies[SESSION_COOKIE_NAME].value

            self.assertIn("HttpOnly", set_cookie)
            self.assertIn("SameSite=Lax", set_cookie)
            self.assertEqual(verify_visitor_token(token), captured_owner_ids[0])

            second_messages = asyncio.run(
                request(f"{SESSION_COOKIE_NAME}={token}")
            )
            self.assertEqual(captured_owner_ids[1], captured_owner_ids[0])
            self.assertNotIn(b"set-cookie", dict(second_messages[0]["headers"]))


if __name__ == "__main__":
    unittest.main()
    validate_admin_security,

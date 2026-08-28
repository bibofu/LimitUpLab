import asyncio
import os
import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from app.security import (
    AnonymousVisitorMiddleware,
    SESSION_COOKIE_NAME,
    resolve_visitor_identity,
    sign_visitor_owner_id,
    validate_session_security,
    verify_visitor_token,
)


class AnonymousSessionSecurityTest(unittest.TestCase):
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

import unittest

from app.models import WebSearchResult
from app.services.web_search import search_web


class WebSearchTest(unittest.TestCase):
    # Regression scenario: provider fallback returns structured evidence.
    def test_provider_fallback_returns_structured_evidence(self) -> None:
        # Simulate the dependency failure required by this regression scenario so its error or
        # fallback path is exercised.
        def failing_loader(_query: str, _limit: int):
            raise RuntimeError("provider unavailable")

        # Prepare the working loader fixture or observation used by the surrounding regression
        # scenario.
        def working_loader(_query: str, _limit: int):
            return [
                WebSearchResult(
                    title="半导体板块新闻",
                    url="https://example.com/semiconductor",
                    domain="example.com",
                    snippet="板块行情相关公开信息。",
                )
            ]

        response = search_web(
            "unit-test-sector-news-query",
            limit=3,
            loaders={"first": failing_loader, "second": working_loader},
        )

        self.assertEqual(response.provider, "second")
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].domain, "example.com")


if __name__ == "__main__":
    unittest.main()

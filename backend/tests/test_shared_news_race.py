"""A slower refresh must serve the newer snapshot saved by another instance."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from app.services.finance_news_latest import LatestFinanceNewsStore
from test_finance_news_latest import news


def test_cold_refresh_race_returns_shared_winner(postgresql_database):
    now = datetime.now(timezone.utc)
    entered, release = Event(), Event()

    def slow_loader():
        entered.set()
        assert release.wait(5)
        return news(now - timedelta(seconds=30), title="Older result")

    slow = LatestFinanceNewsStore(clock=lambda: now, loader=slow_loader)
    fast = LatestFinanceNewsStore(clock=lambda: now, loader=lambda: news(now, title="Newer result"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(slow.read)
        try:
            assert entered.wait(5)
            winner = fast.read()
        finally:
            release.set()
        assert pending.result(timeout=5) == winner
    assert slow.read() == winner
    assert winner[0].items[0].title == "Newer result"
    assert winner[1:] == (False, False, None)

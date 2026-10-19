"""Fast latest-news snapshots, independent of the full historical news scan."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import get_database_path
from app.models import FinanceNewsFacts
from app.services import finance_news


LATEST_NEWS_TTL = timedelta(minutes=5)
SNAPSHOT_RETENTION = timedelta(days=7)
RETRY_DELAY = timedelta(seconds=30)
MAX_SNAPSHOT_BYTES = 1024 * 1024
REFRESH_ERROR = "最新财经新闻暂时无法更新，请稍后重试。"
NewsReadResult = tuple[FinanceNewsFacts, bool, bool, str | None]
_logger = logging.getLogger(__name__)


class LatestFinanceNewsUnavailable(RuntimeError):
    """There is neither a usable snapshot nor a successful latest-news fetch."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_latest(now: datetime) -> FinanceNewsFacts:
    # Explicit one-page loaders bound network work. The larger result limit
    # retains every item from both pages, including low-relevance recent news.
    earliest = now - timedelta(hours=24)
    return finance_news.collect_finance_news(
        limit=2000,
        hours=24,
        now=now,
        loaders={
            "东方财富": lambda: finance_news._load_eastmoney(earliest=earliest, max_pages=1),
            "同花顺": lambda: finance_news._load_tonghuashun(earliest=earliest, max_pages=1),
        },
    )


class LatestFinanceNewsStore:
    """Serve a recent snapshot immediately while one background refresh runs."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        loader: Callable[[], FinanceNewsFacts] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.path = path if path is not None else get_database_path().with_name("finance_news_latest.json")
        self.database_url = os.getenv("LIMITUPLAB_DATABASE_URL", "").strip() if path is None else ""
        self._loader = loader or (lambda: _load_latest(clock()))
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: FinanceNewsFacts | None = None
        self._loaded = False
        self._pending: Future[FinanceNewsFacts] | None = None
        self._retry_at: datetime | None = None
        self._refresh_error: str | None = None

    def read(self) -> NewsReadResult:
        """Return facts, refreshing, stale and a safe refresh error, in that order."""
        now = self._clock()
        with self._lock:
            if not self._loaded or self.database_url:
                self._snapshot = self._read_snapshot(now)
                self._loaded = True
            if self._snapshot is not None and not self._usable(self._snapshot, now):
                self._snapshot = None
            snapshot = self._snapshot
            if snapshot is not None and now - snapshot.fetched_at < LATEST_NEWS_TTL:
                return snapshot.model_copy(deep=True), False, False, None
            if self._pending is not None:
                pending, owner = self._pending, False
            elif self._retry_at is not None and now < self._retry_at:
                if snapshot is None:
                    raise LatestFinanceNewsUnavailable(REFRESH_ERROR)
                return snapshot.model_copy(deep=True), False, True, self._refresh_error
            else:
                pending, owner = Future(), True
                self._pending = pending
                self._refresh_error = None
            old_error = self._refresh_error

        if snapshot is not None:
            if owner:
                try:
                    threading.Thread(
                        target=self._refresh,
                        args=(pending,),
                        name="latest-finance-news",
                        daemon=True,
                    ).start()
                except Exception:
                    self._fail_refresh(pending)
                    return snapshot.model_copy(deep=True), False, True, REFRESH_ERROR
            return snapshot.model_copy(deep=True), True, True, old_error

        # The first ever request has nothing to show. Other cold requests join
        # this same one-page batch instead of starting another provider fetch.
        if owner:
            self._refresh(pending)
        facts = pending.result()
        return facts.model_copy(deep=True), False, self._clock() - facts.fetched_at >= LATEST_NEWS_TTL, None

    def _refresh(self, pending: Future[FinanceNewsFacts]) -> None:
        try:
            facts = self._loader().model_copy(deep=True)
            if not self._usable(facts, self._clock()):
                raise ValueError("unusable latest-news result")
            with self._lock:
                if self._snapshot is not None and facts.fetched_at < self._snapshot.fetched_at:
                    raise ValueError("latest-news result would regress the snapshot")
            self._write_snapshot(facts)
            with self._lock:
                self._snapshot = facts
                self._refresh_error = None
                self._retry_at = None
                self._pending = None
                pending.set_result(facts)
        except Exception:
            self._fail_refresh(pending)

    def _fail_refresh(self, pending: Future[FinanceNewsFacts]) -> None:
        with self._lock:
            self._refresh_error = REFRESH_ERROR
            self._retry_at = self._clock() + RETRY_DELAY
            self._pending = None
            pending.set_exception(LatestFinanceNewsUnavailable(REFRESH_ERROR))

    @staticmethod
    def _usable(facts: FinanceNewsFacts, now: datetime) -> bool:
        return (
            facts.fetched_at.tzinfo is not None
            and timedelta(0) <= now - facts.fetched_at <= SNAPSHOT_RETENTION
            and facts.query is None
            and facts.window_hours == 24
            and bool(facts.sources)
            and len(facts.items) <= 2000
            and all(
                item.published_at.tzinfo is not None
                and facts.fetched_at - timedelta(hours=24) <= item.published_at
                <= facts.fetched_at + timedelta(minutes=5)
                for item in facts.items
            )
        )

    def _read_snapshot(self, now: datetime) -> FinanceNewsFacts | None:
        try:
            if self.database_url:
                from app.services.shared_news_snapshot import read_snapshot
                facts = read_snapshot(self.database_url)
                return facts if facts is not None and self._usable(facts, now) else None
            with self.path.open("rb") as source:
                raw = source.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                return None
            facts = FinanceNewsFacts.model_validate_json(raw)
            return facts if self._usable(facts, now) else None
        except (OSError, ValueError, TypeError):
            return None

    def _write_snapshot(self, facts: FinanceNewsFacts) -> None:
        if self.database_url:
            from app.services.shared_news_snapshot import write_snapshot
            write_snapshot(self.database_url, facts)
            return
        temporary_path: Path | None = None
        try:
            raw = facts.model_dump_json().encode("utf-8")
            if len(raw) > MAX_SNAPSHOT_BYTES:
                raise ValueError("latest-news snapshot exceeds size limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=".finance_news_latest-", suffix=".tmp", delete=False,
            ) as target:
                temporary_path = Path(target.name)
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, ValueError):
            # Disk trouble must not discard valid in-memory news or expose a
            # deployment path through the public refresh status.
            _logger.warning("Latest-news snapshot persistence is unavailable")
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


_store: LatestFinanceNewsStore | None = None
_store_lock = threading.Lock()


def get_latest_news() -> NewsReadResult:
    """Lazily create the process store after runtime configuration is loaded."""
    global _store
    path = get_database_path().with_name("finance_news_latest.json")
    database_url = os.getenv("LIMITUPLAB_DATABASE_URL", "").strip()
    with _store_lock:
        if _store is None or _store.path != path or _store.database_url != database_url:
            _store = LatestFinanceNewsStore(path=None if database_url else path)
        store = _store
    return store.read()

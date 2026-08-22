"""Structured access to Tonghuashun data through hithink-finance CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


HITHINK_SOURCE = "hithink-finance"
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
Runner = Callable[..., subprocess.CompletedProcess[str]]


class HithinkFinanceError(RuntimeError):
    """Raised when the hithink-finance CLI is unavailable or rejects a request."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class HithinkHotStockFact:
    """One normalized stock from the Tonghuashun popularity ranking."""

    symbol: str
    thscode: str
    name: str
    rank: int
    heat: int | None
    rank_change: int | None
    rank_trend: str | None


@dataclass(frozen=True)
class HithinkHotStockSnapshot:
    """One current Tonghuashun hot-stock ranking snapshot."""

    captured_at: datetime
    period: str
    items: list[HithinkHotStockFact]
    source: str = HITHINK_SOURCE


@dataclass(frozen=True)
class HithinkDragonTigerFact:
    """One normalized Dragon-Tiger List row."""

    symbol: str
    thscode: str
    name: str
    change_pct: float | None
    buy_amount: float | None
    sell_amount: float | None
    net_buy_amount: float | None
    net_rate: float | None
    organization_net_buy_amount: float | None
    hot_money_net_buy_amount: float | None
    hot_rank: int | None
    range_days: int | None
    limit_reason: str | None
    concepts: list[str]


@dataclass(frozen=True)
class HithinkDragonTigerSnapshot:
    """Normalized Dragon-Tiger List snapshot for one trade date."""

    trade_date: date | None
    board_type: str
    stock_count: int
    items: list[HithinkDragonTigerFact]
    source: str = HITHINK_SOURCE


@dataclass(frozen=True)
class HithinkLimitUpFact:
    """One stock in the Tonghuashun limit-up pool."""

    symbol: str
    thscode: str
    name: str
    is_st: bool
    is_new: bool
    last_price: float | None
    change_pct: float | None
    limit_up_time: str | None
    limit_up_reason: str | None
    board_height: int
    board_height_text: str | None
    seal_amount: float | None
    max_seal_amount: float | None


@dataclass(frozen=True)
class HithinkLimitUpPoolSnapshot:
    """Normalized page from the Tonghuashun limit-up pool."""

    trade_date: date | None
    page: int
    page_size: int
    total: int
    items: list[HithinkLimitUpFact]
    source: str = HITHINK_SOURCE


class HithinkFinanceCollector:
    """Invoke the official CLI and normalize selected special-data responses."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: float | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds or _configured_timeout()
        self.runner = runner

    def collect_hot_stocks(
        self,
        *,
        period: str = "day",
        limit: int = 30,
    ) -> HithinkHotStockSnapshot:
        """Return the current day/hour popularity ranking."""

        if period not in {"day", "hour"}:
            raise ValueError("period must be day or hour")
        envelope = self._invoke("special", "hot-stock", "--period", period)
        data = _dict(envelope.get("data"))
        rows = _list(data.get("item"))
        items = [
            HithinkHotStockFact(
                symbol=_symbol(row),
                thscode=str(row.get("thscode") or ""),
                name=str(row.get("name") or ""),
                rank=_integer(row.get("rank")) or 0,
                heat=_integer(row.get("heat")),
                rank_change=_integer(row.get("rank_change")),
                rank_trend=_text(row.get("rank_trend")),
            )
            for row in rows
            if _symbol(row) and _integer(row.get("rank")) is not None
        ]
        timestamp = _integer(data.get("timestamp"))
        captured_at = (
            datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            if timestamp is not None
            else datetime.now(timezone.utc)
        )
        return HithinkHotStockSnapshot(
            captured_at=captured_at,
            period=period,
            items=items[: max(1, min(limit, 100))],
        )

    def collect_dragon_tiger(
        self,
        *,
        trade_date: date | None = None,
        board_type: str = "all",
        query: str | None = None,
        limit: int = 100,
    ) -> HithinkDragonTigerSnapshot:
        """Return normalized Dragon-Tiger rows, optionally filtered by stock."""

        if board_type not in {"all", "org", "hot_money"}:
            raise ValueError("board_type must be all, org or hot_money")
        arguments = ["special", "dragon-tiger", "--board-type", board_type]
        if trade_date is not None:
            arguments.extend(["--date", trade_date.isoformat()])
        envelope = self._invoke(*arguments)
        data = _dict(envelope.get("data"))
        raw_items = [*_list(data.get("stock_items")), *_list(data.get("hot_money_items"))]
        normalized_query = (query or "").strip().lower()
        items: list[HithinkDragonTigerFact] = []
        for row in raw_items:
            symbol = _symbol(row)
            name = str(row.get("name") or "")
            if not symbol:
                continue
            if normalized_query and normalized_query not in symbol.lower() and normalized_query not in name.lower():
                continue
            concepts = [
                str(item.get("name"))
                for item in _list(row.get("concept_list"))
                if item.get("name")
            ]
            items.append(
                HithinkDragonTigerFact(
                    symbol=symbol,
                    thscode=str(row.get("thscode") or ""),
                    name=name,
                    change_pct=_ratio_pct(row.get("change")),
                    buy_amount=_number(row.get("buy_value")),
                    sell_amount=_number(row.get("sell_value")),
                    net_buy_amount=_number(row.get("net_value")),
                    net_rate=_ratio_pct(row.get("net_rate")),
                    organization_net_buy_amount=_number(row.get("org_net_value")),
                    hot_money_net_buy_amount=_number(row.get("hot_money_net_value")),
                    hot_rank=_integer(row.get("hot_rank")),
                    range_days=_integer(row.get("range_days")),
                    limit_reason=_text(row.get("limit_reason")),
                    concepts=concepts,
                )
            )
        response_date = _date(data.get("trade_date")) or trade_date
        return HithinkDragonTigerSnapshot(
            trade_date=response_date,
            board_type=board_type,
            stock_count=_integer(data.get("stock_count")) or len({item.symbol for item in items}),
            items=items[: max(1, min(limit, 200))],
        )

    def collect_limit_up_pool(
        self,
        *,
        trade_date: date | None = None,
        page: int = 1,
        size: int = 100,
        sort_field: str = "limit_up_time",
        sort_direction: str = "asc",
    ) -> HithinkLimitUpPoolSnapshot:
        """Return one normalized limit-up pool page."""

        if sort_field not in {"last_price", "continue_day_cnt", "seal_money", "limit_up_time"}:
            raise ValueError("unsupported limit-up sort field")
        if sort_direction not in {"asc", "desc"}:
            raise ValueError("sort_direction must be asc or desc")
        page = max(1, page)
        size = max(1, min(size, 200))
        arguments = [
            "special",
            "limit-up-pool",
            "--page",
            str(page),
            "--size",
            str(size),
            "--sort-field",
            sort_field,
            "--sort-dir",
            sort_direction,
        ]
        if trade_date is not None:
            arguments.extend(["--date-ms", str(_shanghai_midnight_ms(trade_date))])
        envelope = self._invoke(*arguments)
        data = _dict(envelope.get("data"))
        rows = _list(data.get("item"))
        items = [
            HithinkLimitUpFact(
                symbol=_symbol(row),
                thscode=str(row.get("thscode") or ""),
                name=str(row.get("name") or ""),
                is_st=bool(row.get("is_st")),
                is_new=bool(row.get("is_new")),
                last_price=_number(row.get("last_price")),
                change_pct=_number(row.get("price_change_ratio_pct")),
                limit_up_time=_text(row.get("limit_up_time")),
                limit_up_reason=_text(row.get("limit_up_reason")),
                board_height=_integer(row.get("continue_day_cnt")) or 1,
                board_height_text=_text(row.get("continue_day_text")),
                seal_amount=_number(row.get("seal_money")),
                max_seal_amount=_number(row.get("max_seal_money")),
            )
            for row in rows
            if _symbol(row)
        ]
        pagination = _dict(data.get("pagination"))
        return HithinkLimitUpPoolSnapshot(
            trade_date=trade_date,
            page=_integer(pagination.get("page")) or page,
            page_size=_integer(pagination.get("size")) or size,
            total=_integer(pagination.get("total")) or len(items),
            items=items,
        )

    def _invoke(self, *arguments: str) -> dict[str, Any]:
        """Run one CLI command and require a successful JSON envelope."""

        executable = self.executable or _find_executable()
        command = _build_command(executable, [*arguments, "--format", "json"])
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise HithinkFinanceError("hithink-finance CLI is not installed or not on PATH") from error
        except subprocess.TimeoutExpired as error:
            raise HithinkFinanceError(
                f"hithink-finance request timed out after {self.timeout_seconds:g}s",
                code="timeout",
                retryable=True,
            ) from error

        envelope = _parse_envelope(completed.stdout)
        if completed.returncode != 0 or not envelope.get("ok"):
            error_payload = _dict(envelope.get("error"))
            message = _text(error_payload.get("message")) or _text(completed.stderr)
            raise HithinkFinanceError(
                message or f"hithink-finance exited with code {completed.returncode}",
                code=_text(error_payload.get("code")),
                retryable=bool(error_payload.get("retryable")),
            )
        return envelope


def _find_executable() -> str:
    configured = os.getenv("LIMITUPLAB_HITHINK_FINANCE_CLI", "").strip()
    if configured:
        return configured
    executable = shutil.which("hithink-finance.cmd") or shutil.which("hithink-finance")
    if executable:
        return executable
    raise HithinkFinanceError("hithink-finance CLI is not installed or not on PATH")


def _build_command(executable: str, arguments: Sequence[str]) -> list[str]:
    path = Path(executable)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", executable, *arguments]
    return [executable, *arguments]


def _configured_timeout() -> float:
    try:
        return max(1.0, float(os.getenv("LIMITUPLAB_HITHINK_TIMEOUT_SECONDS", "15")))
    except ValueError:
        return 15.0


def _parse_envelope(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise HithinkFinanceError("hithink-finance returned invalid JSON") from error
    if not isinstance(value, dict):
        raise HithinkFinanceError("hithink-finance returned a non-object JSON envelope")
    return value


def _shanghai_midnight_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), SHANGHAI_TIMEZONE).timestamp() * 1000)


def _symbol(row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "").strip()
    if len(ticker) == 6 and ticker.isdigit():
        return ticker
    thscode = str(row.get("thscode") or "").strip()
    candidate = thscode.split(".", 1)[0]
    return candidate if len(candidate) == 6 and candidate.isdigit() else ""


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ratio_pct(value: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(number * 100, 4) if abs(number) <= 2 else round(number, 4)


def _date(value: object) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

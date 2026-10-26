"""Collect one stock's company profile and market-cap fields from AKShare.

The CNInfo profile endpoint does not expose a request timeout, and the local
Windows proxy can reject the Eastmoney endpoint. Both providers therefore run
in a killable child process with a bounded deadline; the child clears proxy
environment variables and asks requests to bypass system proxies. The result
is reduced to plain JSON-compatible values before it crosses the process
boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import math
import os
from typing import Any, Iterator

import requests

from app.collectors.network import without_proxy
from app.collectors.process_timeout import run_in_killable_process


CNINFO_SOURCE = "akshare.stock_profile_cninfo"
EASTMONEY_SOURCE = "akshare.stock_individual_info_em"
TENCENT_SOURCE = "tencent.qt.gtimg.cn"
DEFAULT_CNINFO_TIMEOUT_SECONDS = 12.0
DEFAULT_EASTMONEY_TIMEOUT_SECONDS = 8.0
DEFAULT_TENCENT_TIMEOUT_SECONDS = 6.0


@contextmanager
def _direct_network() -> Iterator[None]:
    """Clear proxy settings for public providers that reject the local proxy."""

    names = ("NO_PROXY", "no_proxy")
    previous = {name: os.environ.get(name) for name in names}
    with without_proxy():
        try:
            os.environ["NO_PROXY"] = "*"
            os.environ["no_proxy"] = "*"
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _plain(value: Any) -> Any:
    """Convert pandas/numpy scalars to JSON-safe values without treating NaN as data."""

    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    text = str(value).strip()
    return None if text in {"", "nan", "NaT", "None", "<NA>"} else text


def _fetch_cninfo_profile(symbol: str) -> dict[str, Any]:
    """Fetch one CNInfo company-profile row in an isolated worker."""

    import akshare as ak

    with _direct_network():
        frame = ak.stock_profile_cninfo(symbol=symbol)
    if frame is None or frame.empty:
        return {}
    record = frame.iloc[0].to_dict()
    return {str(key): _plain(value) for key, value in record.items()}


def _fetch_eastmoney_info(symbol: str) -> dict[str, Any]:
    """Fetch Eastmoney's item/value stock information in an isolated worker."""

    import akshare as ak

    with _direct_network():
        frame = ak.stock_individual_info_em(symbol=symbol, timeout=5)
    if frame is None or frame.empty:
        return {}
    if "item" not in frame.columns or "value" not in frame.columns:
        raise ValueError("Eastmoney stock information did not return item/value columns")
    return {
        str(row["item"]): _plain(row["value"])
        for _, row in frame.iterrows()
        if str(row["item"]).strip()
    }


def _configured_timeout(name: str, default: float) -> float:
    """Read a positive provider deadline from the environment."""

    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def load_cninfo_profile(symbol: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
    """Run the CNInfo profile call with a hard deadline."""

    timeout = timeout_seconds or _configured_timeout(
        "LIMITUPLAB_STOCK_PROFILE_CNINFO_TIMEOUT_SECONDS",
        DEFAULT_CNINFO_TIMEOUT_SECONDS,
    )
    return run_in_killable_process(
        _fetch_cninfo_profile, symbol, timeout_seconds=timeout,
    )


def load_eastmoney_info(symbol: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
    """Run the Eastmoney market-cap call with a hard deadline."""

    timeout = timeout_seconds or _configured_timeout(
        "LIMITUPLAB_STOCK_PROFILE_MARKET_CAP_TIMEOUT_SECONDS",
        DEFAULT_EASTMONEY_TIMEOUT_SECONDS,
    )
    return run_in_killable_process(
        _fetch_eastmoney_info, symbol, timeout_seconds=timeout,
    )


def _tencent_symbol(symbol: str) -> str:
    """Map a six-digit A-share code to Tencent's market-prefixed symbol."""

    if symbol.startswith("6"):
        return "sh" + symbol
    if symbol.startswith(("0", "3")):
        return "sz" + symbol
    if symbol.startswith(("4", "8", "9")):
        return "bj" + symbol
    raise ValueError(f"Unsupported A-share symbol: {symbol}")


def _tencent_number(value: str | None) -> float | None:
    """Parse a positive Tencent numeric field, treating zero/blank as unavailable."""

    text = (value or "").strip()
    if text in {"", "-", "--"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _parse_tencent_market_cap(payload: str, symbol: str) -> dict[str, Any]:
    """Parse market cap/shares from one Tencent quote line.

    Tencent fields 44/45 are circulating/total market value in 亿元. Fields
    72/73/76 carry share counts; 72 and 76 are circulating shares and 73 is
    total shares for the A-share universe observed here.
    """

    quote_start = payload.find('"')
    quote_end = payload.rfind('"')
    if quote_start < 0 or quote_end <= quote_start:
        raise ValueError("Tencent quote did not contain a payload")
    fields = payload[quote_start + 1:quote_end].split("~")
    if len(fields) < 74:
        raise ValueError("Tencent quote payload is too short")
    returned_symbol = fields[2].strip()
    if returned_symbol and returned_symbol != symbol:
        raise ValueError(f"SymbolMismatch: Tencent returned {returned_symbol}")
    total_market_cap = _tencent_number(fields[45])
    float_market_cap = _tencent_number(fields[44])
    total_shares = _tencent_number(fields[73])
    float_shares = _tencent_number(fields[72])
    if float_shares is None and len(fields) > 76:
        float_shares = _tencent_number(fields[76])
    if total_market_cap is not None:
        total_market_cap = round(total_market_cap * 100_000_000.0)
    if float_market_cap is not None:
        float_market_cap = round(float_market_cap * 100_000_000.0)
    if total_shares is not None:
        total_shares = round(total_shares)
    if float_shares is not None:
        float_shares = round(float_shares)
    if all(value is None for value in (
        total_market_cap, float_market_cap, total_shares, float_shares,
    )):
        raise ValueError("Tencent quote did not contain market-cap or share fields")
    return {
        "股票代码": returned_symbol or symbol,
        "股票简称": fields[1].strip() or None,
        "总市值": total_market_cap,
        "流通市值": float_market_cap,
        "总股本": total_shares,
        "流通股": float_shares,
        "__source": TENCENT_SOURCE,
    }


def load_tencent_market_cap(
    symbol: str,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Fetch Tencent's market-cap/share fields without using the local proxy."""

    timeout = timeout_seconds or _configured_timeout(
        "LIMITUPLAB_STOCK_PROFILE_TENCENT_TIMEOUT_SECONDS",
        DEFAULT_TENCENT_TIMEOUT_SECONDS,
    )
    tencent_symbol = _tencent_symbol(symbol)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Referer": "https://gu.qq.com/",
    })
    with _direct_network():
        response = session.get(
            f"https://qt.gtimg.cn/q={tencent_symbol}", timeout=timeout,
        )
    response.raise_for_status()
    return _parse_tencent_market_cap(
        response.content.decode("gbk", errors="replace"), symbol,
    )


def load_market_cap(
    symbol: str,
    *,
    eastmoney_loader=load_eastmoney_info,
    tencent_loader=load_tencent_market_cap,
) -> dict[str, Any]:
    """Use Eastmoney first, then Tencent for the current market-cap snapshot."""

    eastmoney: dict[str, Any] = {}
    eastmoney_error: Exception | None = None
    try:
        loaded = eastmoney_loader(symbol) or {}
        eastmoney = loaded if isinstance(loaded, dict) else {}
    except Exception as error:  # noqa: BLE001 - fallback is expected here
        eastmoney_error = error

    if any(eastmoney.get(key) is not None for key in (
        "总市值", "流通市值", "总股本", "流通股",
    )):
        return {**eastmoney, "__source": EASTMONEY_SOURCE}

    try:
        tencent = tencent_loader(symbol)
    except Exception as tencent_error:
        reason = f"Eastmoney: {eastmoney_error}; Tencent: {tencent_error}"
        raise RuntimeError(reason) from tencent_error
    return {**eastmoney, **tencent, "__source": TENCENT_SOURCE}

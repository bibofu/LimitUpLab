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

from app.collectors.network import without_proxy
from app.collectors.process_timeout import run_in_killable_process


CNINFO_SOURCE = "akshare.stock_profile_cninfo"
EASTMONEY_SOURCE = "akshare.stock_individual_info_em"
DEFAULT_CNINFO_TIMEOUT_SECONDS = 12.0
DEFAULT_EASTMONEY_TIMEOUT_SECONDS = 8.0


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

"""Normalize one A-share company profile and current market-cap snapshot.

CNInfo owns the company profile fields. Eastmoney only supplements the current
market-cap/share fields. A provider failure never hides the other provider's
facts: the payload records field-level gaps in ``data_missing`` and returns
``partial`` instead of inventing values or treating absence as zero.
"""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.collectors.stock_profile_collector import (
    CNINFO_SOURCE,
    EASTMONEY_SOURCE,
    TENCENT_SOURCE,
    load_cninfo_profile,
    load_market_cap,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
PROFILE_TEXT_LIMIT = 500
BUSINESS_SCOPE_LIMIT = 800
REGISTERED_CAPITAL_UNIT = "万元"

ProfileLoader = Callable[[str], dict[str, Any]]
MarketCapLoader = Callable[[str], dict[str, Any]]


def _normalize_symbol(value: str) -> str:
    text = str(value or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[2:]
            break
    if len(text) != 6 or not text.isdigit():
        raise ValueError("stock profile requires a six-digit A-share symbol")
    return text


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text in {"", "nan", "NaT", "None", "<NA>", "-"} else text


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "None", "nan", "NaT", "<NA>"}:
        return None
    for suffix in ("元", "股", "%"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    multiplier = 1.0
    if text.endswith("万亿"):
        multiplier, text = 1_000_000_000_000.0, text[:-2]
    elif text.endswith("亿"):
        multiplier, text = 100_000_000.0, text[:-1]
    elif text.endswith("万"):
        multiplier, text = 10_000.0, text[:-1]
    try:
        number = float(text) * multiplier
    except ValueError:
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if text is None:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        pass
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    return None


def _clip(value: Any, limit: int) -> tuple[str | None, bool]:
    text = _text(value)
    if text is None:
        return None, False
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _error_text(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}".strip()
    return text[:240]


def _market_label(symbol: str) -> str:
    if symbol.startswith("6"):
        return "上交所"
    if symbol.startswith(("0", "3")):
        return "深交所"
    if symbol.startswith(("4", "8", "9")):
        return "北交所"
    return "未知"


def _append_missing(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def build_stock_profile(
    symbol: str,
    *,
    include_market_cap: bool = True,
    fallback_name: str | None = None,
    profile_loader: ProfileLoader = load_cninfo_profile,
    market_cap_loader: MarketCapLoader = load_market_cap,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a normalized company profile with explicit provider gaps."""

    normalized_symbol = _normalize_symbol(symbol)
    current = now or datetime.now(SHANGHAI)

    profile: dict[str, Any] = {}
    market_cap: dict[str, Any] = {}
    profile_error: str | None = None
    market_cap_error: str | None = None

    try:
        loaded = profile_loader(normalized_symbol) or {}
        profile = loaded if isinstance(loaded, dict) else {}
    except Exception as error:  # noqa: BLE001 - provider failures are data gaps
        profile_error = _error_text(error)

    if include_market_cap:
        try:
            loaded = market_cap_loader(normalized_symbol) or {}
            market_cap = loaded if isinstance(loaded, dict) else {}
        except Exception as error:  # noqa: BLE001 - provider failures are data gaps
            market_cap_error = _error_text(error)

    if not profile and profile_error is None:
        profile_error = "EmptyResponse: CNInfo returned no company profile"
    if include_market_cap and not market_cap and market_cap_error is None:
        market_cap_error = "EmptyResponse: Eastmoney returned no stock information"

    profile_code = _text(profile.get("A股代码"))
    if profile_code and profile_code.zfill(6) != normalized_symbol:
        profile_error = f"SymbolMismatch: CNInfo returned {profile_code}"
        profile = {}

    market_symbol = _text(market_cap.get("股票代码"))
    if market_symbol and market_symbol.zfill(6) != normalized_symbol:
        market_cap_error = f"SymbolMismatch: Eastmoney returned {market_symbol}"
        market_cap = {}

    cninfo_name = _text(profile.get("A股简称")) or _text(profile.get("公司名称"))
    eastmoney_name = _text(market_cap.get("股票简称"))
    name = cninfo_name or fallback_name or eastmoney_name or normalized_symbol
    company_name = _text(profile.get("公司名称"))
    market = _text(profile.get("所属市场")) or _market_label(normalized_symbol)

    cninfo_industry = _text(profile.get("所属行业"))
    eastmoney_industry = _text(market_cap.get("行业"))
    if cninfo_industry:
        industry, industry_source = cninfo_industry, "cninfo"
    elif eastmoney_industry:
        industry, industry_source = eastmoney_industry, "eastmoney"
    else:
        industry, industry_source = None, None
    industry_alternatives = (
        [{"value": eastmoney_industry, "source": "eastmoney"}]
        if cninfo_industry and eastmoney_industry and eastmoney_industry != cninfo_industry
        else []
    )

    main_business, main_truncated = _clip(
        profile.get("主营业务"), PROFILE_TEXT_LIMIT,
    )
    business_scope, scope_truncated = _clip(
        profile.get("经营范围"), BUSINESS_SCOPE_LIMIT,
    )
    listing_date = _date_text(profile.get("上市日期"))
    listing_date_source = "cninfo" if listing_date else None
    if listing_date is None:
        listing_date = _date_text(market_cap.get("上市时间"))
        listing_date_source = "eastmoney" if listing_date else None
    established_date = _date_text(profile.get("成立日期"))
    registered_capital = _number(profile.get("注册资金"))

    total_market_cap = _number(market_cap.get("总市值"))
    float_market_cap = _number(market_cap.get("流通市值"))
    total_shares = _number(market_cap.get("总股本"))
    float_shares = _number(market_cap.get("流通股"))
    market_cap_source = market_cap.get("__source") or EASTMONEY_SOURCE
    if market_cap_source == TENCENT_SOURCE:
        market_cap_source_label = "tencent"
    elif market_cap_source == EASTMONEY_SOURCE:
        market_cap_source_label = "eastmoney"
    else:
        market_cap_source_label = "market_cap"

    profile_has_data = bool(profile)
    market_cap_has_data = any(
        value is not None
        for value in (total_market_cap, float_market_cap, total_shares, float_shares)
    )
    profile_empty = bool(profile_error and profile_error.startswith("EmptyResponse"))
    market_cap_empty = bool(
        market_cap_error and market_cap_error.startswith("EmptyResponse")
    )
    profile_status = (
        "ok" if profile_has_data
        else "empty" if profile_empty
        else "unavailable" if profile_error
        else "empty"
    )
    market_cap_status = (
        "not_requested" if not include_market_cap
        else "ok" if market_cap_has_data
        else "empty" if market_cap_empty
        else "unavailable" if market_cap_error
        else "empty"
    )

    data_missing: list[str] = []
    if not profile_has_data:
        _append_missing(data_missing, "profile:cninfo_unavailable")
    else:
        if main_business is None:
            _append_missing(data_missing, "main_business:cninfo_missing")
        if business_scope is None:
            _append_missing(data_missing, "business_scope:cninfo_missing")
        if registered_capital is None:
            _append_missing(data_missing, "registered_capital:cninfo_missing")
    if industry is None:
        _append_missing(data_missing, "industry:missing")
    if listing_date is None:
        _append_missing(data_missing, "listing_date:missing")

    if include_market_cap and not market_cap_has_data:
        reason = (
            f"{market_cap_source_label}_unavailable"
            if market_cap_error else f"{market_cap_source_label}_missing"
        )
        for field in (
            "total_market_cap", "float_market_cap", "total_shares", "float_shares",
        ):
            _append_missing(data_missing, f"{field}:{reason}")
    elif include_market_cap:
        for field, value in (
            ("total_market_cap", total_market_cap),
            ("float_market_cap", float_market_cap),
            ("total_shares", total_shares),
            ("float_shares", float_shares),
        ):
            if value is None:
                _append_missing(
                    data_missing, f"{field}:{market_cap_source_label}_missing",
                )

    if not include_market_cap:
        if profile_status == "unavailable":
            status = "error"
        elif profile_status == "empty":
            status = "empty"
        elif data_missing:
            status = "partial"
        else:
            status = "ok"
    elif profile_status == "unavailable" and market_cap_status == "unavailable":
        status = "error"
    elif profile_status == "empty" and market_cap_status == "empty":
        status = "empty"
    elif data_missing:
        status = "partial"
    else:
        status = "ok"

    source_errors: list[str] = []
    if profile_error:
        source_errors.append(f"cninfo: {profile_error}")
    if market_cap_error:
        source_errors.append(f"eastmoney: {market_cap_error}")

    truncated_fields = [
        name for name, truncated in (
            ("main_business", main_truncated),
            ("business_scope", scope_truncated),
        ) if truncated
    ]
    sources: dict[str, str] = {}
    if profile_has_data:
        sources["profile"] = CNINFO_SOURCE
    if market_cap_has_data:
        sources["market_cap"] = market_cap_source
    if industry_source:
        sources["industry"] = industry_source
    if listing_date_source:
        sources["listing_date"] = listing_date_source

    market_cap_as_of = (
        current.isoformat(timespec="seconds") if market_cap_has_data else None
    )
    return {
        "status": status,
        "symbol": normalized_symbol,
        "name": name,
        "company_name": company_name,
        "market": market,
        "industry": industry,
        "industry_source": industry_source,
        "industry_alternatives": industry_alternatives,
        "main_business": main_business,
        "business_scope": business_scope,
        "listing_date": listing_date,
        "listing_date_source": listing_date_source,
        "established_date": established_date,
        "registered_capital": registered_capital,
        "registered_capital_unit": (
            REGISTERED_CAPITAL_UNIT if registered_capital is not None else None
        ),
        "legal_representative": _text(profile.get("法人代表")),
        "registered_address": _text(profile.get("注册地址")),
        "office_address": _text(profile.get("办公地址")),
        "website": _text(profile.get("官方网站")),
        "total_market_cap": total_market_cap,
        "float_market_cap": float_market_cap,
        "total_shares": total_shares,
        "float_shares": float_shares,
        "profile_status": profile_status,
        "market_cap_status": market_cap_status,
        "profile_as_of": current.date().isoformat(),
        "market_cap_as_of": market_cap_as_of,
        "sources": sources,
        "source_errors": source_errors,
        "data_missing": data_missing,
        "truncated": bool(truncated_fields),
        "truncated_fields": truncated_fields,
    }

"""Normalize Tonghuashun financial statements for the Agent tool surface.

The provider returns year-to-date cumulative quarterly values. This module keeps
that basis explicit and derives single-quarter values only when the previous
quarter in the same fiscal year is present. Balance sheets are point-in-time and
therefore never receive a single-quarter value.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from app.collectors.hithink_finance_collector import HithinkFinanceCollector


STATEMENT_LABELS = {
    "income": "利润表",
    "balance": "资产负债表",
    "cash_flow": "现金流量表",
}
STATEMENT_COLLECTORS = {
    "income": "collect_income_statements",
    "balance": "collect_balance_sheets",
    "cash_flow": "collect_cash_flow_statements",
}
INCOME_FIELDS = (
    "operating_income",
    "operating_costs",
    "operating_expenses",
    "sales_fee",
    "manage_fee",
    "research_and_development_expenses",
    "operating_profit",
    "interest_expenses",
    "profit_total",
    "income_tax_expense",
    "net_profit",
    "parent_holder_net_profit",
    "basic_eps",
)
BALANCE_FIELDS = (
    "assets_total",
    "total_current_assets",
    "non_current_nets_total",
    "cash",
    "accounts_receivable",
    "total_debt",
    "holder_equity_total",
)
CASH_FLOW_FIELDS = (
    "act_cash_flow_net",
    "invest_cash_flow_net",
    "financing_cash_flow_net",
    "pay_fixed_assets_etc_cash",
    "pay_dividends_profits_interest_cash",
    "cash_equivalents_net_addition",
)
STATEMENT_FIELDS = {
    "income": INCOME_FIELDS,
    "balance": BALANCE_FIELDS,
    "cash_flow": CASH_FLOW_FIELDS,
}
SINGLE_QUARTER_STATEMENTS = frozenset({"income", "cash_flow"})
PERIOD_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}
PREVIOUS_PERIOD = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}
UNIT = "元"
CURRENCY = "CNY"
SOURCE = "hithink-finance"
report_date_warning = (
    "report_date为来源记录的报告日期，可能与原始公告日不同；"
    "年度数据可能因追溯调整显示为最新披露日，不据此推断披露时点。"
)


def build_stock_financials_facts(
    *,
    symbol: str,
    statement: str,
    period: str = "quarterly",
    limit: int = 6,
    stock_name: str | None = None,
    collector: HithinkFinanceCollector | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one model-facing financial-statement payload for a single stock."""

    normalized_statement = statement.strip().lower()
    if normalized_statement not in STATEMENT_LABELS:
        raise ValueError("statement must be income, balance or cash_flow")
    normalized_period = period.strip().lower()
    if normalized_period not in {"quarterly", "annual"}:
        raise ValueError("period must be quarterly or annual")
    normalized_limit = max(1, min(limit, 20))
    thscode = _to_thscode(symbol)
    active_collector = collector or HithinkFinanceCollector()
    method_name = STATEMENT_COLLECTORS[normalized_statement]
    facts = sorted(
        getattr(active_collector, method_name)(
            thscode,
            period=normalized_period,
            limit=normalized_limit,
        ),
        key=lambda item: (item.period_end, item.report_date),
        reverse=True,
    )
    rows = [
        _row_payload(
            asdict(fact),
            statement=normalized_statement,
            period=normalized_period,
            peer_rows=facts,
        )
        for fact in facts
    ]
    latest = facts[0] if facts else None
    data_missing: list[str] = []
    warnings: list[str] = []
    if not rows:
        data_missing.append("hithink_finance:no_disclosed_reports")
    if any(
        row.get("fiscal_period") in PREVIOUS_PERIOD
        and _missing_all_single_quarter(row, normalized_statement)
        for row in rows
    ):
        warnings.append("部分季度缺少上一季度累计值，无法推算单季值。")
    warnings.append(report_date_warning)
    return {
        "symbol": _plain_symbol(symbol),
        "stock_name": stock_name,
        "thscode": thscode,
        "statement": normalized_statement,
        "statement_label": STATEMENT_LABELS[normalized_statement],
        "period": normalized_period,
        "period_basis": _period_basis(normalized_statement, normalized_period),
        "currency": CURRENCY,
        "unit": UNIT,
        "source": SOURCE,
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(),
        "data_as_of": latest.period_end.isoformat() if latest else None,
        "requested_limit": normalized_limit,
        "returned_count": len(rows),
        "complete": bool(rows),
        "rows": rows,
        "data_missing": data_missing,
        "warnings": warnings,
        "summary": _summary(
            statement=normalized_statement,
            period=normalized_period,
            stock_name=stock_name,
            symbol=_plain_symbol(symbol),
            thscode=thscode,
            rows=rows,
        ),
    }


def _row_payload(
    fact: dict[str, Any],
    *,
    statement: str,
    period: str,
    peer_rows: list[Any],
) -> dict[str, Any]:
    """Convert one collector fact into a compact row with derived semantics."""

    row: dict[str, Any] = {
        "thscode": fact["thscode"],
        "fiscal_year": fact["fiscal_year"],
        "fiscal_period": fact["fiscal_period"],
        "report_date": fact["report_date"].isoformat(),
        "period_end": fact["period_end"].isoformat(),
    }
    for field in STATEMENT_FIELDS[statement]:
        value = fact.get(field)
        if value is not None:
            row[field] = value
    if statement in SINGLE_QUARTER_STATEMENTS and period == "quarterly":
        previous = _previous_quarter_values(fact, peer_rows)
        for field in STATEMENT_FIELDS[statement]:
            value = fact.get(field)
            if value is None:
                continue
            single = _single_quarter_value(fact, field, value, previous)
            if single is not None:
                row[f"{field}_single_quarter"] = single
    return row


def _previous_quarter_values(fact: dict[str, Any], peer_rows: list[Any]) -> dict[str, Any] | None:
    """Return the same fiscal year's previous quarter, if it is available."""

    previous_period = PREVIOUS_PERIOD.get(fact["fiscal_period"])
    if previous_period is None:
        return None
    for peer in peer_rows:
        if (
            peer.fiscal_year == fact["fiscal_year"]
            and peer.fiscal_period == previous_period
        ):
            return asdict(peer)
    return None


def _single_quarter_value(
    fact: dict[str, Any],
    field: str,
    value: float,
    previous: dict[str, Any] | None,
) -> float | None:
    """Derive one single-quarter value without inventing a missing base."""

    if fact["fiscal_period"] == "Q1":
        return value
    if previous is None:
        return None
    previous_value = previous.get(field)
    if previous_value is None:
        return None
    return round(value - previous_value, 4)


def _missing_all_single_quarter(row: dict[str, Any], statement: str) -> bool:
    """Report a missing single-quarter base only when no field could be derived."""

    fields = [f"{field}_single_quarter" for field in STATEMENT_FIELDS[statement]]
    return not any(field in row for field in fields)


def _period_basis(statement: str, period: str) -> str:
    if statement == "balance":
        return "资产负债表为期末时点值，不适用累计/单季口径。"
    if period == "annual":
        return "FY为全年累计；不提供单季推算。"
    return "Q1-Q4为年初至今累计；*_single_quarter为单季推算值（本期累计减上期累计）。"


def _summary(
    *,
    statement: str,
    period: str,
    stock_name: str | None,
    symbol: str,
    thscode: str,
    rows: list[dict[str, Any]],
) -> str:
    """Build a compact, source-bounded summary without inventing missing values."""

    label = f"{stock_name}({thscode})" if stock_name else thscode
    if not rows:
        return f"{label} 未取得已披露{STATEMENT_LABELS[statement]}。"
    latest = rows[0]
    period_label = f"{latest['fiscal_year']} {latest['fiscal_period']}"
    if statement == "income":
        metrics = [
            _metric("累计营收", latest.get("operating_income")),
            _metric("累计归母净利润", latest.get("parent_holder_net_profit")),
        ]
        if period == "quarterly":
            metrics.extend([
                _metric("单季营收", latest.get("operating_income_single_quarter")),
                _metric("单季归母净利润", latest.get("parent_holder_net_profit_single_quarter")),
            ])
        return f"{label} {period_label} 利润表：" + _join_metrics(metrics)
    if statement == "balance":
        metrics = [
            _metric("资产总计", latest.get("assets_total")),
            _metric("负债合计", latest.get("total_debt")),
            _metric("所有者权益合计", latest.get("holder_equity_total")),
        ]
        return f"{label} {period_label} 资产负债表：" + _join_metrics(metrics)
    metrics = [
        _metric("经营现金流净额", latest.get("act_cash_flow_net")),
        _metric("投资现金流净额", latest.get("invest_cash_flow_net")),
        _metric("筹资现金流净额", latest.get("financing_cash_flow_net")),
    ]
    if period == "quarterly":
        metrics.extend([
            _metric("单季经营现金流净额", latest.get("act_cash_flow_net_single_quarter")),
        ])
    return f"{label} {period_label} 现金流量表：" + _join_metrics(metrics)


def _metric(label: str, value: Any) -> str | None:
    if value is None:
        return None
    return f"{label} {value / 100_000_000:,.2f} 亿元"


def _join_metrics(metrics: list[str | None]) -> str:
    values = [metric for metric in metrics if metric]
    return "，".join(values) + "。" if values else "来源未返回关键金额。"


def _plain_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    if len(value) != 6 or not value.isdigit():
        raise ValueError("stock symbol must be a 6-digit A-share code")
    return value


def _to_thscode(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        code, suffix = value.rsplit(".", 1)
        if len(code) == 6 and code.isdigit() and suffix:
            return f"{code}.{suffix}"
    code = _plain_symbol(value)
    if code.startswith(("4", "8", "920")):
        suffix = "BJ"
    elif code.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"

"""Execution handlers for post-limit screening, paths and statistics."""

from __future__ import annotations

from typing import Any

from app.post_limit_query_contract import build_post_limit_query_contract
from datetime import date

from .context import ExecutionState
from .helpers import _resolve_tool_stock_target, _tool_error_trace


def catalog(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    _run(state, name, arguments, state.tools.strategy_catalog)


def latest(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    _run(
        state,
        name,
        arguments,
        lambda: state.tools.strategy_latest(
            str(arguments.get("strategy_id") or ""),
            _date(arguments.get("data_as_of")),
        ),
    )


def strategy_stock_path(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    try:
        symbol = _resolve_tool_stock_target(
            tools=state.tools,
            request=state.request,
            argument_value=str(arguments.get("symbol") or "").strip() or None,
            context_symbol=state.context_symbol,
        )
    except Exception as error:  # noqa: BLE001
        _error(state, name, arguments, str(error))
        return
    _run(
        state,
        name,
        arguments,
        lambda: state.tools.strategy_stock_path(
            str(arguments.get("strategy_id") or ""),
            symbol,
            _date(arguments.get("data_as_of")),
        ),
    )


def registered_statistics(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    ids = arguments.get("strategy_ids")
    strategy_ids = [str(item) for item in ids] if isinstance(ids, list) else []
    _run(
        state,
        name,
        arguments,
        lambda: state.tools.strategy_statistics(
            strategy_ids,
            data_as_of=_date(arguments.get("data_as_of")),
            days=int(arguments.get("days") or 30),
        ),
    )


def screen(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    contract = build_post_limit_query_contract(
        state.request.message,
        request_trade_date=state.request.trade_date,
        planner_arguments=arguments,
    )
    _run(state, name, arguments, lambda: state.tools.post_limit_screen(contract))


def path(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    contract = build_post_limit_query_contract(
        state.request.message,
        request_trade_date=state.request.trade_date,
        planner_arguments={**arguments, "mode": "path"},
    )
    try:
        symbol = _resolve_tool_stock_target(
            tools=state.tools,
            request=state.request,
            argument_value=str(arguments.get("symbol") or "").strip() or None,
            context_symbol=state.context_symbol,
        )
    except Exception as error:  # noqa: BLE001
        _error(state, name, arguments, str(error))
        return
    _run(state, name, arguments, lambda: state.tools.post_limit_path(contract, symbol))


def statistics(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    contract = build_post_limit_query_contract(
        state.request.message,
        request_trade_date=state.request.trade_date,
        planner_arguments={**arguments, "mode": "statistics"},
    )
    _run(state, name, arguments, lambda: state.tools.post_limit_statistics(contract))


def _run(state: ExecutionState, name: str, arguments: dict[str, Any], operation) -> None:
    try:
        result = operation()
    except Exception as error:  # noqa: BLE001
        _error(state, name, arguments, str(error))
        return
    state.facts[name] = result.output
    state.traces.append(result.trace())
    state.call_names.append(name)
    if isinstance(result.output, dict) and result.output.get("data_as_of"):
        state.references.append(f"data_as_of={result.output['data_as_of']}")
    if isinstance(result.output, dict) and result.output.get("rule_version"):
        state.references.append(f"rule_version={result.output['rule_version']}")


def _error(state: ExecutionState, name: str, arguments: dict[str, Any], error: str) -> None:
    state.facts[f"{name}_error"] = error
    state.traces.append(_tool_error_trace(
        name=name,
        tool_input=arguments,
        summary="涨停后研究数据查询失败。",
        error=error,
    ))
    state.call_names.append(name)


def _date(value: object) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))

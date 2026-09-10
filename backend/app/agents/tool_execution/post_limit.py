"""Execution handlers for post-limit screening, paths and statistics."""

from __future__ import annotations

from typing import Any

from app.post_limit_query_contract import build_post_limit_query_contract

from .context import ExecutionState
from .helpers import _resolve_tool_stock_target, _tool_error_trace


# Execute the screen evidence step, resolving request arguments and recording its facts and trace.
def screen(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    contract = build_post_limit_query_contract(
        state.request.message,
        request_trade_date=state.request.trade_date,
        planner_arguments=arguments,
    )
    # The callback defers state.tools.post_limit_screen until its wrapper invokes it, preserving
    # the surrounding request's arguments.
    _run(state, name, arguments, lambda: state.tools.post_limit_screen(contract))


# Execute the path evidence step, resolving request arguments and recording its facts and trace.
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
    # The callback defers state.tools.post_limit_path until its wrapper invokes it, preserving the
    # surrounding request's arguments.
    _run(state, name, arguments, lambda: state.tools.post_limit_path(contract, symbol))


# Execute the statistics evidence step, resolving request arguments and recording its facts and
# trace.
def statistics(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    contract = build_post_limit_query_contract(
        state.request.message,
        request_trade_date=state.request.trade_date,
        planner_arguments={**arguments, "mode": "statistics"},
    )
    # The callback defers state.tools.post_limit_statistics until its wrapper invokes it,
    # preserving the surrounding request's arguments.
    _run(state, name, arguments, lambda: state.tools.post_limit_statistics(contract))


# Call the selected post-limit registry method and add its contract, facts and trace to execution
# state.
def _run(state: ExecutionState, name: str, arguments: dict[str, Any], operation) -> None:
    try:
        result = operation()
    except Exception as error:  # noqa: BLE001
        _error(state, name, arguments, str(error))
        return
    state.facts[name] = result.output
    state.traces.append(result.trace())
    state.call_names.append(name)
    if result.output.get("data_as_of"):
        state.references.append(f"data_as_of={result.output['data_as_of']}")
    if result.output.get("rule_version"):
        state.references.append(f"rule_version={result.output['rule_version']}")


# Record a post-limit query failure in facts and trace so the answer stage can report missing
# evidence.
def _error(state: ExecutionState, name: str, arguments: dict[str, Any], error: str) -> None:
    state.facts[f"{name}_error"] = error
    state.traces.append(_tool_error_trace(
        name=name,
        tool_input=arguments,
        summary="涨停后研究数据查询失败。",
        error=error,
    ))
    state.call_names.append(name)

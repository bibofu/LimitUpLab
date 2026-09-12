"""Strict registered-tool dispatch without whole-query semantic rewrites."""

import inspect
from datetime import date
from typing import get_type_hints

from app.agents.tool_policy import AgentToolPolicyEngine
from app.post_limit_query_contract import PostLimitQueryContract

OUTPUT_COLLECTIONS = {
    "first_board_ratings": ("top_candidates", "symbol"),
    "limit_up_events": ("events", "symbol"),
    "hot_stock_ranking": ("items", "symbol"),
    "sector_performance": ("top_sectors", "sector_name"),
    "sector_stock_ranking": ("stocks", "symbol"),
    "daily_board_promotion": ("items", "trade_date"),
    "stock_news": ("items", "title"),
    "finance_news": ("items", "title"),
}


def evidence_payload(result):
    """Stable public evidence shape; preserve full ratings, avoid model repr strings."""
    from fastapi.encoders import jsonable_encoder
    raw = jsonable_encoder(result.output)
    def named_metrics(value):
        if isinstance(value, list):
            return [named_metrics(item) for item in value]
        if isinstance(value, dict):
            value = {key: named_metrics(item) for key, item in value.items()}
            metric = value.get("metric")
            if isinstance(metric, str) and metric.isidentifier() and "value" in value:
                value.setdefault(metric, value["value"])
        return value
    raw = named_metrics(raw)
    trace = jsonable_encoder(result.trace_output)
    if result.name == "first_board_ratings" and isinstance(raw, dict) and "candidates" in raw:
        return {**raw, **trace, "top_candidates": [
            {**item.get("facts", {}), **{k: v for k, v in item.items() if k != "facts"}}
            for item in raw["candidates"]
        ]}
    if isinstance(raw, list) and trace:
        return trace
    return raw if isinstance(raw, (dict, list)) else trace


def schemas_for_runtime(tools):
    """Expose Python-required parameters omitted by the legacy planner schema."""
    from copy import deepcopy
    from app.agents.tools import AgentToolRegistry
    result = []
    for schema in tools.schemas():
        item = deepcopy(schema.model_dump())
        if schema.name in OUTPUT_COLLECTIONS:
            collection, entity = OUTPUT_COLLECTIONS[schema.name]
            item["output_contract"] = {"collection_path": collection, "entity_path": f"{collection}.*.{entity}"}
        method = getattr(AgentToolRegistry, schema.name, None)
        if method and not schema.name.startswith("post_limit_"):
            required = set(item["args_schema"].get("required", []))
            required.update(k for k, p in inspect.signature(method).parameters.items()
                            if k != "self" and p.default is inspect.Parameter.empty)
            item["args_schema"]["required"] = sorted(required)
        result.append(item)
    return result


def invoke(tools, capability, name, arguments):
    errors = AgentToolPolicyEngine(tools).validate_calls(
        [{"name": name, "arguments": arguments}], capability=capability,
    )
    if errors:
        raise ValueError("; ".join(errors))
    arguments = dict(arguments)
    resolver = getattr(tools, "resolve_stock_identity", None)
    if callable(resolver):
        if isinstance(arguments.get("symbol"), str):
            arguments["symbol"] = resolver(arguments["symbol"])[0]
        if isinstance(arguments.get("symbols"), list):
            arguments["symbols"] = [resolver(value)[0] for value in arguments["symbols"]]
    if callable(getattr(tools, "execute_frozen_calls", None)):
        from app.agents.tools import ToolResult
        from app.models import AgentChatRequest
        execution = tools.execute_frozen_calls(
            [{"name": name, "arguments": dict(arguments)}],
            request=AgentChatRequest(session_id="task-frozen", message=""),
        )
        trace = execution["tool_results"][0]
        return ToolResult(
            name=name, input=trace.input, output=trace.output, summary=trace.summary,
            status=trace.status, trace_output=trace.output, error=trace.error,
            result_status=trace.result.status if trace.result else None,
        )
    kwargs = dict(arguments)
    # These are transport adaptations of registered schemas, not query heuristics.
    if name in {"post_limit_screen", "post_limit_path", "post_limit_statistics"}:
        for key in ("anchor_date", "data_as_of"):
            if kwargs.get(key):
                kwargs[key] = date.fromisoformat(kwargs[key])
        if "shapes" in kwargs:
            kwargs["shapes"] = tuple(kwargs["shapes"])
        mode = {"post_limit_screen": "screen", "post_limit_path": "path", "post_limit_statistics": "statistics"}[name]
        contract = PostLimitQueryContract(mode=mode, **kwargs)
        if name == "post_limit_path":
            if not kwargs.get("symbol"):
                raise ValueError("post_limit_path requires a resolved symbol")
            return tools.post_limit_path(contract, symbol=kwargs["symbol"])
        return getattr(tools, name)(contract)
    method = getattr(tools, name)
    # Preserve explicit date arguments, including historical dates distinct from UI context.
    hints = get_type_hints(method)
    for key, value in kwargs.items():
        if isinstance(value, str) and "datetime.date" in str(hints.get(key)):
            kwargs[key] = date.fromisoformat(value)
    if name == "limit_up_events":
        # Result mode is presentation metadata; underlying query always returns rows.
        kwargs.pop("result_mode", None)
    if name == "stock_kline" and not isinstance(kwargs.get("symbol"), str):
        raise ValueError("use fan_out for multiple stock_kline entities")
    inspect.signature(method).bind(**kwargs)
    return method(**kwargs)

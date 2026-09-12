"""Strict registered-tool dispatch without whole-query semantic rewrites."""

import inspect
from datetime import date
from typing import get_type_hints

from app.agents.tool_policy import AgentToolPolicyEngine
from app.post_limit_query_contract import PostLimitQueryContract


def schemas_for_runtime(tools):
    """Expose Python-required parameters omitted by the legacy planner schema."""
    from copy import deepcopy
    from app.agents.tools import AgentToolRegistry
    result = []
    for schema in tools.schemas():
        item = deepcopy(schema.model_dump())
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

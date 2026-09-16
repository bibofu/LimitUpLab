"""Policy and invocation shared by every ReAct turn; never infer extra calls."""

from copy import deepcopy
from datetime import date

import inspect
from typing import get_type_hints

from app.agents.query_contract import current_query_reference_date
from app.agents.react_runtime.catalog import structured_tools
from app.agents.tools import ToolResult
from app.post_limit_query_contract import PostLimitQueryContract
from app.agents.react_runtime.contracts import CONTROL_MODELS
from app.agents.react_runtime.evidence import payload_of


class ToolGateway:
    def __init__(self, registry, evidence):
        self.registry, self.evidence = registry, evidence
        self.contracts = {contract.name: contract for contract in registry.schemas()}
        self.structured = structured_tools(registry, self._invoke)
        self.schemas = {
            name: {
                **self.contracts[name].model_dump(),
                "args_schema": tool.args_schema.model_json_schema(),
            }
            for name, tool in self.structured.items()
        }

    def definitions(self):
        definitions = []
        for name, tool in self.structured.items():
            parameters = tool.args_schema.model_json_schema()
            parameters["additionalProperties"] = False
            definitions.append({"type": "function", "function": {
                "name": name, "description": tool.description,
                "parameters": parameters,
            }})
        for name, (model, description) in CONTROL_MODELS.items():
            definitions.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": model.model_json_schema(),
            }})
        return definitions

    def validate(self, call):
        name, args = call["name"], deepcopy(call["args"])
        if name in CONTROL_MODELS:
            return CONTROL_MODELS[name][0].model_validate(args).model_dump()
        if name not in self.schemas or not self.registry.is_enabled(name):
            raise ValueError("Tool not registered for this research profile")
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
        if name == "stock_kline" and not isinstance(args.get("symbol"), str):
            raise ValueError("symbol must be one stock; issue independent calls for multiple stocks")
        required = self.contracts[name].args_schema.get("required", [])
        missing = [key for key in required if args.get(key) is None]
        if missing:
            raise ValueError("; ".join(f"Missing required field: {key}" for key in missing))
        try:
            args = self.structured[name].args_schema.model_validate(args).model_dump(
                exclude_unset=True
            )
        except Exception as error:
            raise ValueError(f"Invalid tool arguments: {error}") from error
        for key in (*self.contracts[name].dates, "requested_as_of"):
            if args.get(key):
                date.fromisoformat(args[key])
        if args.get("start_date") and args.get("end_date") and args["start_date"] > args["end_date"]:
            raise ValueError("start_date must not be after end_date")
        requested_as_of = args.get("requested_as_of")
        if requested_as_of:
            mode = self.contracts[name].time_mode
            effective = max((e.trade_date for e in self.registry.events), default=current_query_reference_date()) if mode == "latest_local" else current_query_reference_date()
            if requested_as_of != effective.isoformat():
                raise ValueError(f"This tool cannot serve that historical scope; supported as-of: {effective}")
        return args

    def execute(self, name, args):
        kwargs = dict(args)
        kwargs.pop("requested_as_of", None)
        if callable(getattr(self.registry, "execute_frozen_calls", None)):
            from app.models import AgentChatRequest
            execution = self.registry.execute_frozen_calls(
                [{"name": name, "arguments": kwargs}], request=AgentChatRequest(session_id="react-frozen", message=""),
            )
            trace = execution["tool_results"][0]
            # Frozen recordings are already canonical Gateway observations. Do not
            # flatten ratings again or turn a recorded error/partial into empty.
            outcome = trace.result
            if outcome is None:
                raise ValueError("Frozen tool result requires an explicit outcome")
            payload = execution.get("observation_payloads", [trace.output])[0]
            result = ToolResult(name=name, input=trace.input, output=payload, summary=trace.summary,
                                trace_output=trace.output, status=trace.status, error=trace.error,
                                result_status=outcome.status, data_fresh=outcome.data_fresh,
                                source_errors=tuple(outcome.source_errors))
            return result, payload, outcome.status
        else:
            result = self.structured[name].invoke(kwargs)
        payload = payload_of(result)
        if not isinstance(payload, (dict, list)):
            raise ValueError("Invalid tool output: expected structured facts")
        trace = result.trace()
        state = trace.result.status if trace.result else ("error" if trace.status == "error" else "ok")
        if name == "limit_up_events" and not payload.get("events"):
            state = "empty"
        return result, payload, state

    def _invoke(self, name, **kwargs):
        # Generated models use None to represent omitted optional fields;
        # implementations retain their own reviewed defaults when those fields
        # were not supplied by the model.
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        if self.contracts[name].adapter == "first_board_filter":
            rated = self.registry.first_board_ratings(trade_date=date.fromisoformat(kwargs["trade_date"]) if kwargs.get("trade_date") else None)
            payload = payload_of(rated)
            query = kwargs["query"].strip().casefold()
            if not query:
                raise ValueError("query must not be empty")
            rows = [row for row in payload["top_candidates"] if any(query in str(row.get(k, "")).casefold()
                    for k in ("symbol", "name", "industry", "concept"))]
            output = {"items": rows, "trade_date": payload.get("trade_date"), "matched_count": len(rows), "source": "first-board-ratings"}
            return ToolResult(name=name, input=kwargs, output=output, summary=f"评级名单筛选返回 {len(rows)} 项", result_status="ok" if rows else "empty")
        resolver = getattr(self.registry, "resolve_stock_identity", None)
        if callable(resolver):
            if kwargs.get("symbol"):
                kwargs["symbol"] = resolver(kwargs["symbol"])[0]
            if kwargs.get("symbols") is not None:
                if not kwargs["symbols"]:
                    raise ValueError("Empty symbol set must not become an unrestricted query")
                kwargs["symbols"] = [resolver(value)[0] for value in kwargs["symbols"]]
        if self.contracts[name].adapter == "post_limit":
            for key in self.contracts[name].dates:
                if kwargs.get(key): kwargs[key] = date.fromisoformat(kwargs[key])
            if "shapes" in kwargs: kwargs["shapes"] = tuple(kwargs["shapes"])
            contract = PostLimitQueryContract(mode=name.removeprefix("post_limit_"), **kwargs)
            method = getattr(self.registry, name)
            return method(contract, symbol=kwargs["symbol"]) if name == "post_limit_path" else method(contract)
        method = getattr(self.registry, name)
        hints = get_type_hints(method)
        for key in self.contracts[name].dates:
            if kwargs.get(key) and "datetime.date" in str(hints.get(key)):
                kwargs[key] = date.fromisoformat(kwargs[key])
        inspect.signature(method).bind(**kwargs)
        return method(**kwargs)

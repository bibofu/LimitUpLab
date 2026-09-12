"""Policy and invocation shared by every ReAct turn; never infer extra calls."""

from copy import deepcopy
from datetime import date

import inspect
from typing import get_type_hints

from app.agents.query_contract import current_query_reference_date
from app.agents.react_runtime.catalog import CATALOG, schemas
from app.agents.tool_policy import _validate_schema_value
from app.agents.tools import ToolResult
from app.post_limit_query_contract import PostLimitQueryContract
from app.agents.react_runtime.contracts import CONTROL_MODELS
from app.agents.react_runtime.evidence import payload_of


class ToolGateway:
    def __init__(self, registry, evidence):
        self.registry, self.evidence = registry, evidence
        self.schemas = schemas(registry)

    def definitions(self):
        definitions = []
        for name, schema in self.schemas.items():
            parameters = deepcopy(schema["args_schema"])
            parameters["additionalProperties"] = False
            definitions.append({"type": "function", "function": {
                "name": name, "description": schema["description"] + " Returns: " + schema["returns"],
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
        if name == "stock_kline" and not isinstance(args.get("symbol"), str):
            raise ValueError("symbol must be one stock; issue independent calls for multiple stocks")
        schema = self.schemas[name]["args_schema"]
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
        if set(args) - set(schema["properties"]):
            raise ValueError("Unknown arguments: " + str(sorted(set(args) - set(schema["properties"]))))
        errors = [f"Missing required field: {k}" for k in schema.get("required", []) if args.get(k) is None]
        errors.extend(f"{key}: {error}" for key, value in args.items()
                      for error in _validate_schema_value(value, schema["properties"][key]))
        if errors:
            raise ValueError("; ".join(errors))
        for key in (*CATALOG[name].dates, "requested_as_of"):
            if args.get(key):
                date.fromisoformat(args[key])
        if args.get("start_date") and args.get("end_date") and args["start_date"] > args["end_date"]:
            raise ValueError("start_date must not be after end_date")
        requested_as_of = args.get("requested_as_of")
        if requested_as_of:
            mode = CATALOG[name].time_mode
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
            result = ToolResult(name=name, input=trace.input, output=trace.output, summary=trace.summary,
                                status=trace.status, error=trace.error, result_status=trace.result.status if trace.result else None)
        else:
            result = self._invoke(name, kwargs)
        payload = payload_of(result)
        if not isinstance(payload, (dict, list)):
            raise ValueError("Invalid tool output: expected structured facts")
        trace = result.trace()
        state = trace.result.status if trace.result else ("error" if trace.status == "error" else "ok")
        if name == "limit_up_events" and not payload.get("events"):
            state = "empty"
        return result, payload, state

    def _invoke(self, name, kwargs):
        if name == "first_board_filter":
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
        if name.startswith("post_limit_"):
            for key in CATALOG[name].dates:
                if kwargs.get(key): kwargs[key] = date.fromisoformat(kwargs[key])
            if "shapes" in kwargs: kwargs["shapes"] = tuple(kwargs["shapes"])
            contract = PostLimitQueryContract(mode=name.removeprefix("post_limit_"), **kwargs)
            method = getattr(self.registry, name)
            return method(contract, symbol=kwargs["symbol"]) if name == "post_limit_path" else method(contract)
        method = getattr(self.registry, name)
        hints = get_type_hints(method)
        for key in CATALOG[name].dates:
            if kwargs.get(key) and "datetime.date" in str(hints.get(key)):
                kwargs[key] = date.fromisoformat(kwargs[key])
        if name == "limit_up_events": kwargs.pop("result_mode", None)
        inspect.signature(method).bind(**kwargs)
        return method(**kwargs)

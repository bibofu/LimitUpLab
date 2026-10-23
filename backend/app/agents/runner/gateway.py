"""Business-only tool validation and execution for the lightweight runner.

Unlike the legacy gateway this has no control tools, no EvidenceStore and no
DateScope binding. Dates stay explicit in the model's tool arguments; the
gateway still checks ISO dates and verifies that returned dates echo the request.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import inspect
from typing import get_type_hints

from app.agents.query_contract import current_query_reference_date
from app.agents.react_runtime.catalog import CURRENT_TIME_MODES, structured_tools
from app.agents.react_runtime.evidence import payload_of
from app.post_limit_query_contract import PostLimitQueryContract


class BusinessToolGateway:
    """Schema-validated access to business tools only."""

    def __init__(self, registry):
        self.registry = registry
        self.contracts = {contract.name: contract for contract in registry.schemas()}
        self.structured = structured_tools(registry, self._invoke)

    def validate(self, call: dict) -> dict:
        name, args = call["name"], deepcopy(call["args"])
        if name not in self.contracts or not self.registry.is_enabled(name):
            raise ValueError("Tool not registered for this research profile")
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
        contract = self.contracts[name]
        if name == "stock_kline" and not isinstance(args.get("symbol"), str):
            raise ValueError("symbol must be one stock; issue independent calls for multiple stocks")
        required = contract.args_schema.get("required", [])
        missing = [key for key in required if args.get(key) is None]
        if missing:
            raise ValueError("; ".join(f"Missing required field: {key}" for key in missing))
        try:
            args = self.structured[name].args_schema.model_validate(args).model_dump(
                exclude_unset=True
            )
        except Exception as error:
            raise ValueError(f"Invalid tool arguments: {error}") from error
        for key in (*contract.dates, "requested_as_of"):
            if args.get(key):
                date.fromisoformat(args[key])
        if args.get("start_date") and args.get("end_date") and args["start_date"] > args["end_date"]:
            raise ValueError("start_date must not be after end_date")
        requested_as_of = args.get("requested_as_of")
        if requested_as_of:
            mode = contract.time_mode
            effective = (
                max((item.trade_date for item in self.registry.events), default=current_query_reference_date())
                if mode == "latest_local" else current_query_reference_date()
            )
            if requested_as_of != effective.isoformat():
                raise ValueError(f"This tool cannot serve that historical scope; supported as-of: {effective}")
        return args

    def execute(self, name: str, args: dict):
        kwargs = dict(args)
        kwargs.pop("requested_as_of", None)
        result = self.structured[name].invoke(kwargs)
        payload = payload_of(result)
        if not isinstance(payload, (dict, list)):
            raise ValueError("Invalid tool output: expected structured facts")
        self._assert_requested_dates(name, args, payload)
        trace = result.trace()
        state = trace.result.status if trace.result else ("error" if trace.status == "error" else "ok")
        if name == "limit_up_events" and state == "ok" and not payload.get("events"):
            state = "empty"
        return result, payload, state

    def _assert_requested_dates(self, name: str, args: dict, payload) -> None:
        """Reject a provider response that silently falls back to another date."""

        if not isinstance(payload, dict):
            return
        fields = self.contracts[name].dates
        if self.contracts[name].time_mode in CURRENT_TIME_MODES:
            fields = (*fields, "requested_as_of")
        for field in fields:
            expected = args.get(field)
            if not expected:
                continue
            actual_keys = [field]
            if field == "end_date":
                actual_keys = ["data_as_of", "end_date"]
            elif field == "trade_date" and name == "sector_performance":
                actual_keys = ["trade_date", "data_as_of"]
            elif field == "requested_as_of":
                actual_keys = (
                    [] if self.contracts[name].time_mode == "latest_local_and_current"
                    else ["data_as_of", "trade_date"]
                )
            for key in actual_keys:
                actual = payload.get(key)
                if actual is not None and str(actual) != str(expected):
                    raise ValueError(f"Tool returned {actual} but the request requires {expected}")

    def _invoke(self, name: str, **kwargs):
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        if self.contracts[name].adapter == "first_board_filter":
            rated = self.registry.first_board_ratings(
                trade_date=date.fromisoformat(kwargs["trade_date"]) if kwargs.get("trade_date") else None
            )
            payload = payload_of(rated)
            query = kwargs["query"].strip().casefold()
            if not query:
                raise ValueError("query must not be empty")
            rows = [
                row for row in payload["top_candidates"]
                if any(query in str(row.get(key, "")).casefold()
                       for key in ("symbol", "name", "industry", "concept"))
            ]
            output = {
                "items": rows,
                "trade_date": payload.get("trade_date"),
                "matched_count": len(rows),
                "source": "first-board-ratings",
            }
            from app.agents.tools import ToolResult
            return ToolResult(
                name=name, input=kwargs, output=output,
                summary=f"评级名单筛选返回 {len(rows)} 项",
                result_status="ok" if rows else "empty",
            )
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
                if kwargs.get(key):
                    kwargs[key] = date.fromisoformat(kwargs[key])
            if "shapes" in kwargs:
                kwargs["shapes"] = tuple(kwargs["shapes"])
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

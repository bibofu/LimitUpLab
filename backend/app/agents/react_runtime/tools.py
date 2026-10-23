"""Policy and invocation shared by every ReAct turn; never infer extra calls."""

from copy import deepcopy
from datetime import date

import inspect
from typing import get_type_hints

from app.agents.query_contract import current_query_reference_date
from app.agents.react_runtime.catalog import CURRENT_TIME_MODES, structured_tools
from app.agents.tools import ToolResult
from app.post_limit_query_contract import PostLimitQueryContract
from app.agents.react_runtime.contracts import CONTROL_MODELS
from app.agents.react_runtime.evidence import payload_of
from app.agents.react_runtime.date_scope import DateScope


class ToolGateway:
    def __init__(self, registry, evidence=None):
        self.registry, self.evidence = registry, evidence
        self.date_scope = DateScope()
        self.contracts = {contract.name: contract for contract in registry.schemas()}
        self.structured = structured_tools(registry, self._invoke)
        self.schemas = {
            name: {
                **self.contracts[name].model_dump(),
                "args_schema": tool.args_schema.model_json_schema(),
            }
            for name, tool in self.structured.items()
        }

    @property
    def required_date(self) -> date | None:
        """Compatibility for old single-date checkpoints."""
        return self.date_scope.single_date

    @required_date.setter
    def required_date(self, value: date | None):
        self.date_scope = DateScope(mode="dates", dates=[value]) if value else DateScope()

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
        contract = self.contracts[name]
        if name == "recent_limit_up_returns" and self.date_scope.basis == "event" and self.date_scope.mode != "unspecified":
            raise ValueError("This rolling-cohort tool cannot constrain explicit event dates; use an event-aware query")
        date_fields = (("requested_as_of",) if contract.time_mode in CURRENT_TIME_MODES
                       else contract.dates)
        if self.date_scope.basis == "event" and contract.adapter == "post_limit":
            if name != "post_limit_path" and self.date_scope.mode != "unspecified":
                raise ValueError("This post-limit tool cannot constrain the requested event dates; use an event-aware query")
            args = self.date_scope.bind(("anchor_date",), args) if name == "post_limit_path" else args
            args = self._bind_observation_cutoff(("data_as_of",), args)
        elif self.date_scope.basis == "event" and contract.time_mode in CURRENT_TIME_MODES and self.date_scope.observation_cutoff:
            args = self._bind_observation_cutoff(date_fields, args)
        elif self.date_scope.basis == "event" and contract.dates == ("end_date",):
            # Event dates select the cohort, not the end of the following K-line
            # window. Without an explicit cutoff, the model must select a real
            # observation window; never invent a future trading-day endpoint.
            args = self._bind_observation_cutoff(date_fields, args)
        else:
            args = self.date_scope.bind(date_fields, args)
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
        for key in (*contract.dates, "requested_as_of"):
            if args.get(key):
                date.fromisoformat(args[key])
        if args.get("start_date") and args.get("end_date") and args["start_date"] > args["end_date"]:
            raise ValueError("start_date must not be after end_date")
        requested_as_of = args.get("requested_as_of")
        if requested_as_of:
            mode = contract.time_mode
            effective = max((e.trade_date for e in self.registry.events), default=current_query_reference_date()) if mode == "latest_local" else current_query_reference_date()
            if requested_as_of != effective.isoformat():
                raise ValueError(f"This tool cannot serve that historical scope; supported as-of: {effective}")
        return args

    def _bind_observation_cutoff(self, fields, args):
        cutoff = self.date_scope.observation_cutoff
        if cutoff is None:
            return args
        return DateScope(mode="dates", dates=[cutoff]).bind(fields, args)

    def execute(self, name, args):
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

    def _assert_requested_dates(self, name, args, payload):
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
                # Sector snapshots expose the requested observation day under
                # data_as_of; other tools may use it for a distinct cutoff.
                actual_keys = ["trade_date", "data_as_of"]
            elif field == "requested_as_of":
                # stock_activity deliberately combines current news and the
                # latest available local close. Its data_as_of dates the K-lines,
                # not the whole current retrieval (e.g. before today's close).
                actual_keys = ([] if self.contracts[name].time_mode == "latest_local_and_current"
                               else ["data_as_of", "trade_date"])
            for key in actual_keys:
                actual = payload.get(key)
                if actual is not None and str(actual) != str(expected):
                    raise ValueError(
                        f"Tool returned {actual} but the request requires {expected}"
                    )

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

"""Policy and invocation shared by every ReAct turn; never infer extra calls."""

from copy import deepcopy
from datetime import date

from app.agents.capability_contract import CAPABILITIES
from app.agents.task_runtime.adapter import invoke, schemas_for_runtime
from app.agents.tool_policy import AgentToolPolicyEngine
from app.agents.react_runtime.contracts import CONTROL_MODELS
from app.agents.react_runtime.evidence import payload_of


class ToolGateway:
    def __init__(self, registry, evidence):
        self.registry, self.evidence = registry, evidence
        self.capabilities = {
            requirement.name: capability.name for capability in CAPABILITIES
            for requirement in capability.required_tools
        }
        self.schemas = {s["name"]: s for s in schemas_for_runtime(registry)}
        # The legacy planner supports array shorthand; the underlying method is
        # single-entity. Expose its actual contract in the native ReAct loop.
        if "stock_kline" in self.schemas:
            self.schemas["stock_kline"]["args_schema"]["properties"]["symbol"] = {
                "type": "string", "description": "One stock code or exact name. For multiple stocks issue independent calls.",
            }

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
        if name not in self.schemas or name not in self.capabilities:
            raise ValueError("Tool not registered for this research profile")
        if name == "stock_kline" and not isinstance(args.get("symbol"), str):
            raise ValueError("symbol must be one stock; issue independent calls for multiple stocks")
        errors = AgentToolPolicyEngine(self.registry).validate_calls(
            [{"name": name, "arguments": args}], capability=self.capabilities[name],
        )
        missing = [k for k in self.schemas[name]["args_schema"].get("required", []) if args.get(k) is None]
        if errors or missing:
            raise ValueError("; ".join(errors + ([f"Missing required: {missing}"] if missing else [])))
        for key in ("date", "trade_date", "start_date", "end_date", "anchor_date", "data_as_of"):
            if args.get(key):
                date.fromisoformat(args[key])
        return args

    def execute(self, name, args):
        result = invoke(self.registry, self.capabilities[name], name, args)
        payload = payload_of(result)
        trace = result.trace()
        state = trace.result.status if trace.result else ("error" if trace.status == "error" else "ok")
        if name == "limit_up_events" and not payload.get("events"):
            state = "empty"
        return result, payload, state

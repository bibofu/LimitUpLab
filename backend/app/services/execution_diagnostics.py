"""Bounded exception locations without paths, messages, request data or frame locals."""


STAGES = {"input_context", "input_review", "session_context", "agent_execution", "journal_finish",
          "budget_reservation", "provider_invocation", "unknown"}
ORIGINS = {"budget_persistence", "provider_invocation"}
ERROR_TYPES = {"PermissionError", "FileNotFoundError", "OSError", "TimeoutError", "RuntimeError",
               "ValueError", "TypeError", "ValidationError", "NativeFunctionCallingError",
               "BudgetExceeded", "BudgetPersistenceError", "InterruptedError"}
LOCATIONS = {
    "app.agents.react_runtime.runtime": {"run", "Run.agent"},
    "app.agents.react_runtime.context": {"prepare_task_context"},
    "app.services.prompt_security": {"review_input"},
    "app.services.langchain_provider": {"LangChainChatProvider.generate_messages", "LangChainChatProvider._run"},
    "app.services.session_memory": {"prepare_session_context"},
    "evals.golden.runner": {"Budget.take", "BudgetedProvider._invoke", "run_case"},
    "evals.golden.reporting": {"save_report"},
}


def mark_execution_origin(error, *, origin, provider_entered):
    """Internal markers describe the observed call boundary, not server receipt."""
    previous = getattr(error, "execution_origin", None)
    if isinstance(previous, dict) and previous.get("origin") == "budget_persistence":
        return  # An outer provider wrapper must not erase an inner pre-provider failure.
    error.execution_origin = {"origin": origin, "provider_entered": provider_entered}


def exception_diagnostics(error, *, stage):
    result = {"stage": stage if stage in STAGES else "unknown"}
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 4:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__
    result["exception_types"] = [type(item).__name__ if type(item).__name__ in ERROR_TYPES else "OtherError"
                                 for item in chain]
    locations = []
    for item in chain:
        marker = getattr(item, "execution_origin", None)
        if isinstance(marker, dict) and isinstance(marker.get("origin"), str) and marker["origin"] in ORIGINS:
            result.setdefault("origin", marker["origin"])
            if type(marker.get("provider_entered")) is bool:
                result.setdefault("provider_entered", marker["provider_entered"])
        if isinstance(item, OSError):
            for key in ("errno", "winerror"):
                value = getattr(item, key, None)
                if type(value) is int:
                    result.setdefault(key, value)
        trace = item.__traceback__
        while trace is not None:
            module = trace.tb_frame.f_globals.get("__name__")
            name = trace.tb_frame.f_code.co_qualname
            if name in LOCATIONS.get(module, set()):
                location = f"{module}.{name}"
                if location not in locations and len(locations) < 12:
                    locations.append(location)
            trace = trace.tb_next
    result["locations"] = locations
    return result

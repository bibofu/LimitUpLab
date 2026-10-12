"""Count every issued phase attempt, including recovered protocol failures."""

from collections import Counter


PHASES = {"final_delivery", "visible_audit"}
PROTOCOL_ERRORS = {"NativeFunctionCallingError", "ValidationError", "ValueError"}


def protocol_metrics(records, *, errors_key, diagnostics_key):
    protocol, dimensions, affected_phases = Counter(), Counter(), Counter()
    phase_calls = repair_calls = recovered_dimensions = 0
    diagnostics_complete = all(diagnostics_key in record for record in records)

    def collect(phase, errors, diagnostics, issued):
        affected = False
        for key, kind in errors.items():
            if key == phase and kind in PROTOCOL_ERRORS:
                # Generic provider ValueError can describe transport/configuration.
                if diagnostics.get("failure_category") != "request":
                    protocol[f"{phase}:{kind}"] += 1
                    affected = True
            elif key.startswith(phase + "."):
                dimensions[f"{key}:{kind}"] += 1
                if kind == "ValidationError":
                    affected = True
        if affected:
            affected_phases[phase] += 1
        return int(issued)

    for record in records:
        errors = record.get(errors_key) or {}
        diagnostics = record.get(diagnostics_key) or {}
        for phase in PHASES:
            detail = diagnostics.get(phase) or {}
            attempts = detail.get("attempts")
            if isinstance(attempts, list) and attempts:
                recovered_dimensions += len(detail.get("recovered_dimensions", []))
                for attempt in attempts:
                    issued = attempt.get("request_issued") is True
                    phase_calls += collect(phase, attempt.get("errors", {}), attempt.get("diagnostics", {}), issued)
                    repair_calls += int(issued and attempt.get("attempt", 1) > 1)
            else:
                # Old reports have one attempt per phase and no recovery history.
                issued = phase in diagnostics and errors.get(phase) not in {"BudgetExceeded", "BudgetPersistenceError"}
                phase_calls += collect(phase, errors, detail, issued)
    count = sum(protocol.values())
    affected_count = sum(affected_phases.values())
    return {"judge_phase_protocol_errors": dict(protocol), "judge_phase_protocol_error_count": count,
            "judge_protocol_affected_phases": dict(affected_phases),
            "judge_protocol_affected_phase_count": affected_count,
            "judge_protocol_affected_phase_rate": affected_count / phase_calls
                if diagnostics_complete and phase_calls else None,
            "judge_dimension_errors": dict(dimensions), "judge_dimension_error_count": sum(dimensions.values()),
            "judge_phase_call_count": phase_calls if diagnostics_complete else None,
            "judge_schema_repair_call_count": repair_calls if diagnostics_complete else None,
            "judge_schema_recovered_dimension_count": recovered_dimensions,
            "judge_phase_protocol_error_rate": count / phase_calls if diagnostics_complete and phase_calls else None}

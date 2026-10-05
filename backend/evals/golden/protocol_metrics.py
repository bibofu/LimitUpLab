"""Whole-phase protocol failures and per-check review errors have separate denominators."""

from collections import Counter


PHASES = {"final_delivery", "visible_audit"}
PROTOCOL_ERRORS = {"NativeFunctionCallingError", "ValidationError", "ValueError"}


def protocol_metrics(records, *, errors_key, diagnostics_key):
    protocol, dimensions, affected_phases = Counter(), Counter(), Counter()
    phase_calls = 0
    diagnostics_complete = all(diagnostics_key in record for record in records)
    for record in records:
        errors = record.get(errors_key) or {}
        diagnostics = record.get(diagnostics_key) or {}
        affected = set()
        phase_calls += sum(phase in diagnostics and errors.get(phase) not in
                           {"BudgetExceeded", "BudgetPersistenceError"} for phase in PHASES)
        for phase, kind in errors.items():
            if phase in PHASES and kind in PROTOCOL_ERRORS:
                # A provider-raised generic ValueError may describe a transport/config issue.
                # Legacy reports lack this distinction and retain type-based counts only.
                if diagnostics.get(phase, {}).get("failure_category") != "request":
                    protocol[f"{phase}:{kind}"] += 1
                    affected.add(phase)
            elif phase.partition(".")[0] in PHASES and "." in phase:
                dimensions[f"{phase}:{kind}"] += 1
                if kind == "ValidationError":
                    affected.add(phase.partition(".")[0])
        affected_phases.update(affected)
    count = sum(protocol.values())
    affected_count = sum(affected_phases.values())
    return {"judge_phase_protocol_errors": dict(protocol), "judge_phase_protocol_error_count": count,
            "judge_protocol_affected_phases": dict(affected_phases),
            "judge_protocol_affected_phase_count": affected_count,
            "judge_protocol_affected_phase_rate": affected_count / phase_calls
                if diagnostics_complete and phase_calls else None,
            "judge_dimension_errors": dict(dimensions), "judge_dimension_error_count": sum(dimensions.values()),
            "judge_phase_call_count": phase_calls if diagnostics_complete else None,
            "judge_phase_protocol_error_rate": count / phase_calls if diagnostics_complete and phase_calls else None}

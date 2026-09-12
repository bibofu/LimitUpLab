"""Closed, deterministic collection algebra; no Python/SQL/JSONPath execution."""

from .contracts import values_at


def evaluate(payload, predicate):
    """Return (matched, evaluable); a missing path is not a false observation."""
    values = values_at(payload, predicate.path.removeprefix("payload."))
    if not values:
        return False, False
    if predicate.operator == "empty":
        return all(value == [] for value in values), True
    if predicate.operator == "not_empty":
        return any(value != [] for value in values), True
    if predicate.operator in {"equals", "contains"}:
        return predicate.value in values, True
    if predicate.operator == "not_equals":
        return predicate.value not in values, True
    if predicate.operator in {"greater_than", "less_than"}:
        if not isinstance(predicate.value, (int, float)):
            raise ValueError("numeric comparison requires numeric value")
        numbers = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if not numbers:
            return False, False
        return any(value > predicate.value if predicate.operator == "greater_than" else value < predicate.value for value in numbers), True
    raise ValueError("unsupported predicate")


def matches(payload, predicate):
    return evaluate(payload, predicate)[0]


def select(payload, step):
    selected = values_at(payload, step.select_path.removeprefix("payload.")) if step.select_path else [payload]
    if not selected:
        raise ValueError("selection path missing; not an empty query")
    rows = selected[0] if len(selected) == 1 and isinstance(selected[0], list) else selected
    filtered = []
    for row in rows:
        decisions = [evaluate(row, predicate) for predicate in step.filters]
        if any(not evaluable for _matched, evaluable in decisions):
            raise ValueError("filter field missing or not comparable")
        if all(matched for matched, _evaluable in decisions):
            filtered.append(row)
    rows = filtered
    if step.sort_field:
        if any(len(values_at(row, step.sort_field)) != 1 for row in rows):
            raise ValueError("sort field missing or ambiguous")
        rows = sorted(rows, key=lambda row: values_at(row, step.sort_field)[0], reverse=step.sort_descending)
    return rows[:step.take] if step.take is not None else rows

"""Schema validation without query routing or implicit tool repair."""

import re
from typing import Any


def validate_schema_value(value: Any, schema: dict[str, Any]) -> list[str]:
    """Validate the small JSON-Schema subset used by registered Agent tools."""

    allowed = schema.get("type")
    allowed_types = set(allowed if isinstance(allowed, list) else [allowed])
    if value is None:
        return [] if "null" in allowed_types else ["null is not allowed"]
    checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "array": lambda item: isinstance(item, (list, tuple)),
        "object": lambda item: isinstance(item, dict),
    }
    if allowed_types and not any(checks.get(kind, lambda _item: False)(value) for kind in allowed_types):
        return [f"expected {sorted(allowed_types)}, got {type(value).__name__}"]
    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"value is outside enum {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"must be <= {schema['maximum']}")
    if isinstance(value, (list, tuple)):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"must contain <= {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(f"item {index}: {error}" for error in validate_schema_value(item, item_schema))
    if isinstance(value, str) and schema.get("pattern") and not re.fullmatch(str(schema["pattern"]), value):
        errors.append("does not match required pattern")
    return errors

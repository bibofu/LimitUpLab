"""Send self-contained schemas without changing their validation contract."""

from copy import deepcopy


def inline_local_refs(schema):
    """Resolve local definitions; reject cycles and external references explicitly."""
    definitions = schema.get("$defs", {})

    def expand(node, stack=()):
        if isinstance(node, list):
            return [expand(item, stack) for item in node]
        if not isinstance(node, dict):
            return deepcopy(node)
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise ValueError("Judge schema requires a local definition")
            name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
            if name not in definitions or name in stack:
                raise ValueError("Missing or recursive judge schema definition")
            # A reference and sibling validation keywords mean conjunction, not
            # replacement. Generated judge schemas only use annotation siblings.
            if set(node) - {"$ref", "title", "description", "default", "examples"}:
                raise ValueError("Judge schema reference has unsupported constraint siblings")
            expanded = expand(definitions[name], (*stack, name))
            expanded.update({key: expand(value, stack) for key, value in node.items() if key != "$ref"})
            return expanded
        return {key: expand(value, stack) for key, value in node.items() if key != "$defs"}

    return expand(schema)

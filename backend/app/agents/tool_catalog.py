"""Typed LangChain tools derived from the canonical registry contracts."""

from dataclasses import MISSING, fields
from datetime import date
from functools import partial
import inspect
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from app.agents.tools import AgentToolRegistry, AgentToolSchema, TOOL_SCHEMAS
from app.post_limit_query_contract import PostLimitQueryContract


CURRENT_TIME_MODES = {"current", "latest_local", "latest_local_and_current"}


class ToolArguments(BaseModel):
    """Strict base for every generated tool-input model."""

    model_config = ConfigDict(extra="forbid", strict=True)


def _field_annotation(schema: dict[str, Any]) -> Any:
    """Translate the reviewed JSON subset into a Pydantic field annotation."""

    if "enum" in schema:
        return Literal.__getitem__(tuple(schema["enum"]))
    declared = schema.get("type")
    kinds = declared if isinstance(declared, list) else [declared]
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict[str, Any],
    }
    annotations: list[Any] = []
    for kind in kinds:
        if kind == "null":
            annotations.append(type(None))
        elif kind == "array":
            annotations.append(list[_field_annotation(schema.get("items", {}))])
        else:
            annotations.append(mapping.get(kind, Any))
    if not annotations:
        return Any
    result = annotations[0]
    for annotation in annotations[1:]:
        result = result | annotation
    return result


def arguments_model(contract: AgentToolSchema) -> type[BaseModel]:
    """Generate the only runtime validator and LLM parameter schema."""

    source = contract.args_schema
    properties = dict(source.get("properties", {}))
    if contract.time_mode in CURRENT_TIME_MODES:
        properties["requested_as_of"] = {
            "type": ["string", "null"],
            "description": (
                "If the user requests an as-of date, supply it for temporal capability "
                "validation. Unsupported historical scope is rejected, never rewritten."
            ),
        }
    required = set(source.get("required", []))
    fields: dict[str, tuple[Any, Any]] = {}
    for name, schema in properties.items():
        field = Field(
            default=_field_default(contract, name, schema, name in required),
            description=schema.get("description"),
            ge=schema.get("minimum"),
            le=schema.get("maximum"),
            pattern=schema.get("pattern"),
            max_length=schema.get("maxItems"),
        )
        fields[name] = (_field_annotation(schema), field)
    model_name = "".join(part.title() for part in contract.name.split("_")) + "Arguments"
    return create_model(model_name, __base__=ToolArguments, **fields)


def _field_default(
    contract: AgentToolSchema,
    name: str,
    schema: dict[str, Any],
    required: bool,
) -> Any:
    """Derive callable defaults so they cannot drift from the typed tool."""

    if required:
        return ...
    if contract.adapter == "direct":
        parameter = inspect.signature(
            getattr(AgentToolRegistry, contract.name)
        ).parameters.get(name)
        if parameter and parameter.default is not inspect.Parameter.empty:
            return parameter.default
    if contract.adapter == "post_limit":
        field = next((item for item in fields(PostLimitQueryContract) if item.name == name), None)
        if field is not None and field.default is not MISSING:
            if schema.get("type") == "array" and isinstance(field.default, tuple):
                return list(field.default)
            return field.default
    return None


def structured_tools(registry, invoke) -> dict[str, StructuredTool]:
    """Bind enabled canonical contracts to one validated LangChain tool surface."""

    result: dict[str, StructuredTool] = {}
    for contract in registry.schemas():
        description = (
            f"{contract.description} Time capability: {contract.time_mode}. "
            f"{contract.notes} Returns: {contract.returns}"
        )
        result[contract.name] = StructuredTool.from_function(
            func=partial(invoke, contract.name),
            name=contract.name,
            description=description,
            args_schema=arguments_model(contract),
            metadata={
                "time_mode": contract.time_mode,
                "dates": contract.dates,
                "collection": contract.collection,
                "adapter": contract.adapter,
            },
        )
    return result


def _assert_direct_implementation(contract: AgentToolSchema) -> None:
    """Fail import when a direct implementation drifts from its public contract."""

    if contract.adapter != "direct":
        return
    method = getattr(AgentToolRegistry, contract.name, None)
    if method is None:
        raise RuntimeError(f"Missing tool implementation: {contract.name}")
    parameters = {
        name: value
        for name, value in inspect.signature(method).parameters.items()
        if name != "self"
    }
    public = set(contract.args_schema.get("properties", {}))
    if public != set(parameters):
        raise RuntimeError(
            f"Tool contract/signature drift for {contract.name}: "
            f"contract={sorted(public)}, implementation={sorted(parameters)}"
        )
    contract_required = set(contract.args_schema.get("required", []))
    implementation_required = {
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    if contract_required != implementation_required:
        raise RuntimeError(
            f"Tool required-field drift for {contract.name}: "
            f"contract={sorted(contract_required)}, implementation={sorted(implementation_required)}"
        )
    hints = get_type_hints(method)
    for name, schema in contract.args_schema.get("properties", {}).items():
        contract_kinds = _schema_kinds(schema)
        implementation_kinds = _annotation_kinds(hints.get(name, Any))
        if contract_kinds != implementation_kinds:
            raise RuntimeError(
                f"Tool parameter-type drift for {contract.name}.{name}: "
                f"contract={sorted(contract_kinds)}, "
                f"implementation={sorted(implementation_kinds)}"
            )


def _schema_kinds(schema: dict[str, Any]) -> set[str]:
    declared = schema.get("type")
    kinds = set(declared if isinstance(declared, list) else [declared])
    kinds.discard("null")
    return {"number" if kind == "integer_or_number" else kind for kind in kinds}


def _annotation_kinds(annotation: Any) -> set[str]:
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        return set().union(*(
            _annotation_kinds(item)
            for item in get_args(annotation)
            if item is not type(None)
        ))
    if origin is list:
        return {"array"}
    if annotation in {str, date}:
        return {"string"}
    if annotation is int:
        return {"integer"}
    if annotation is float:
        return {"number"}
    if annotation is bool:
        return {"boolean"}
    if annotation is Any:
        return {"any"}
    return {str(annotation)}


for _contract in TOOL_SCHEMAS:
    _assert_direct_implementation(_contract)


# Compatibility name for read-only callers; values are the canonical contracts.
CATALOG = {contract.name: contract for contract in TOOL_SCHEMAS}

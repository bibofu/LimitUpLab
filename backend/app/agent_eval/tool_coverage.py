"""Static coverage registration checks; never execute tools or certify model quality."""

from collections import Counter
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.tools import TOOL_SCHEMAS, V1_CLOSED_MARKET_TOOL_NAMES


DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "evals/tool_coverage.json"
NonBlank = Annotated[str, Field(min_length=1)]


class CoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    tool: NonBlank
    scope: Literal["local_priority", "external_dependency"]
    status: Literal["planned", "partial", "blocked", "deferred"]
    notes: NonBlank
    coverage_refs: list[NonBlank]

    @model_validator(mode="after")
    def validate_status(self):
        if (self.status == "partial") != bool(self.coverage_refs):
            raise ValueError("only partial coverage must declare nonempty coverage_refs")
        return self


class CoverageRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["agent-tool-coverage-v1"]
    tools: list[CoverageEntry]


def preflight_tool_coverage(path: Path = DEFAULT_REGISTRY):
    """Compare registration with the production schemas without constructing a registry."""
    report = {
        "schema_version": "agent-tool-coverage-preflight-v1",
        "registration_valid": False,
        "model_calls": 0, "business_tool_calls": 0,
        "data_readiness": "not_checked", "quality_verified": False,
        "coverage_refs_verified": False, "release_eligible": False,
        "errors": [],
    }
    try:
        book = CoverageRegistry.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        report["errors"] = [{"code": "invalid_registry", "detail": str(error)}]
        return report
    registered = {schema.name for schema in TOOL_SCHEMAS}
    counts = Counter(item.tool for item in book.tools)
    for code, names in (
        ("missing_tools", sorted(registered - counts.keys())),
        ("unknown_tools", sorted(counts.keys() - registered)),
        ("duplicate_tools", sorted(name for name, count in counts.items() if count > 1)),
    ):
        if names:
            report["errors"].append({"code": code, "tools": names})
    report["registration_valid"] = not report["errors"]
    report["registered_tool_count"] = len(registered)
    report["declared_entry_count"] = len(book.tools)
    report["declared_counts"] = {
        scope: dict(sorted(Counter(item.status for item in book.tools if item.scope == scope).items()))
        for scope in ("local_priority", "external_dependency")
    }
    report["tools"] = [
        {**item.model_dump(), "enabled_profiles": (
            ["v1_close_review", "extended"] if item.tool in V1_CLOSED_MARKET_TOOL_NAMES
            else ["extended"] if item.tool in registered else []
        )} for item in book.tools
    ]
    return report


def write_coverage_report(path: Path, report: dict):
    # Explicit output only; exclusive creation preserves previous reports.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

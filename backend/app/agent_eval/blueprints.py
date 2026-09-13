"""Non-runnable question blueprints and honest planned-coverage inventory."""

from collections import Counter
import json
import hashlib
from pathlib import Path
from string import Formatter
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from app.agent_eval.models import Contract, Identifier, Profile
from app.agents.react_runtime.catalog import arguments_model
from app.agents.tools import TOOL_SCHEMAS, V1_CLOSED_MARKET_TOOL_NAMES


class Blueprint(Contract):
    id: Identifier
    mode: Literal["offline", "live_historical", "live_current", "live_external_canary"] = "offline"
    profile: Profile = "v1_close_review"
    question: Identifier
    focus_tool: str | None
    tools: list[Identifier]
    arguments: dict[str, dict] = Field(default_factory=dict)
    bindings: dict[str, Identifier] = Field(default_factory=dict)
    scenario: Identifier = "normal_complete"
    capabilities: list[Identifier] = Field(min_length=1)
    requirements: list[Identifier] = Field(min_length=1)
    checks: list[Identifier] = Field(min_length=1)
    data_needs: Identifier
    terminal_policy: Identifier


class BlueprintBook(Contract):
    schema_version: Literal["agent-blueprints-v1"]
    maturity: Literal["blueprint"]
    cases: list[Blueprint] = Field(min_length=1)


def _tokens(value):
    if isinstance(value, str):
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None:
                if not name.isidentifier() or spec or conversion:
                    raise ValueError("only plain named binding slots are allowed")
                yield name
    elif isinstance(value, dict):
        for item in value.values():
            yield from _tokens(item)
    elif isinstance(value, list):
        for item in value:
            yield from _tokens(item)


def load_blueprints(path: Path) -> BlueprintBook:
    book = BlueprintBook.model_validate_json(path.read_text(encoding="utf-8"))
    catalog = {item.name: item for item in TOOL_SCHEMAS}
    ids = [item.id for item in book.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate blueprint ID")
    identities = set()
    for item in book.cases:
        identity = (item.mode, item.profile, item.question)
        if identity in identities:
            raise ValueError("duplicate question in the same suite/profile")
        identities.add(identity)
        if item.focus_tool is not None and item.focus_tool not in item.tools:
            raise ValueError("focus tool must be listed in planned tool coverage")
        if len(item.tools) != len(set(item.tools)) or not set(item.tools) <= catalog.keys():
            raise ValueError("unknown or duplicate planned business tool")
        if item.profile == "v1_close_review" and not set(item.tools) <= V1_CLOSED_MARKET_TOOL_NAMES:
            raise ValueError("blueprint tool violates profile boundary")
        if not set(item.arguments) <= set(item.tools):
            raise ValueError("parameter plan references an unlisted tool")
        slots = set(_tokens(item.question)) | set(_tokens(item.arguments))
        if slots != set(item.bindings):
            raise ValueError("binding slots must exactly match declared binding requirements")
        for tool, values in item.arguments.items():
            model = arguments_model(catalog[tool])
            if not set(values) <= model.model_fields.keys():
                raise ValueError("unknown critical parameter in blueprint")
            for name, value in values.items():
                if not set(_tokens(value)):
                    field = model.model_fields[name]
                    annotation = Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
                    TypeAdapter(annotation).validate_python(value, strict=True)
            # Bindings are intentionally unresolved, never fabricated into market facts.
            if not set(_tokens(values)):
                model.model_validate(values)
        if item.mode == "live_historical" and any(
                catalog[tool].time_mode in {"current", "latest_local", "latest_local_and_current", "retrieved_now"}
                for tool in item.tools):
            raise ValueError("historical live cannot use current-only tools as historical evidence")
    return book


def coverage(book: BlueprintBook) -> dict:
    rows = []
    for tool in TOOL_SCHEMAS:
        by_profile = {}
        for profile in ("v1_close_review", "extended"):
            enabled = profile == "extended" or tool.name in V1_CLOSED_MARKET_TOOL_NAMES
            related = [c for c in book.cases if c.profile == profile and tool.name in c.tools]
            planned_args = {arg for c in related for arg in c.arguments.get(tool.name, {})}
            by_profile[profile] = {
                "enabled": enabled,
                "offline_blueprints": [c.id for c in related if c.mode == "offline"],
                "live_blueprints": [c.id for c in related if c.mode != "offline"],
                "primary_normal_offline": [c.id for c in related if c.mode == "offline"
                                           and c.focus_tool == tool.name and c.scenario == "normal_complete"],
                "planned_parameters": sorted(planned_args),
                "parameters_without_explicit_plan": sorted(set(arguments_model(tool).model_fields) - planned_args),
                "asset_readiness": "not_assessed",
            }
        rows.append({"tool": tool.name, "time_mode": tool.time_mode, "profiles": by_profile})
    gaps = {profile: [row["tool"] for row in rows if row["profiles"][profile]["enabled"]
                     and not row["profiles"][profile]["primary_normal_offline"]]
            for profile in ("v1_close_review", "extended")}
    return {"scope": "planned_coverage_only", "runnable": False, "release_eligible": False,
            "blueprint_digest": "sha256:" + hashlib.sha256(book.model_dump_json().encode("utf-8")).hexdigest(),
            "counts": dict(Counter(c.mode for c in book.cases)),
            "profile_counts": dict(Counter(c.profile for c in book.cases)),
            "primary_normal_offline_gaps": gaps, "tools": rows}


def save_blueprint_report(book: BlueprintBook, destination: Path) -> dict:
    report = coverage(book)
    lines = ["# 评测题目蓝图与录制需求", "", "仅为出题计划，不是可执行 Case 或已通过评测的覆盖证明。", ""]
    for mode, title in [("offline", "Offline Core"), ("live_historical", "Historical Live"),
                        ("live_current", "Current Invariant"), ("live_external_canary", "External Canary")]:
        lines += [f"## {title}：{report['counts'].get(mode, 0)}道", ""]
        for item in book.cases:
            if item.mode != mode:
                continue
            lines += [f"### {item.id} · {item.profile}", "", item.question, "",
                "交付项：" + "；".join(item.requirements), "",
                "校验意图：" + "；".join(item.checks), "",
                "数据/录制需求：" + item.data_needs, "",
                "终态口径：" + item.terminal_policy, ""]
            if item.bindings:
                lines += ["待绑定：" + "；".join(f"{key}：{value}" for key, value in item.bindings.items()), ""]
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "coverage.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    with (destination / "questions.md").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return {key: value for key, value in report.items() if key != "tools"}

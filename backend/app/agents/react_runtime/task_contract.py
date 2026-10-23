"""Semantic task interpretation and deterministic presentation validation."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.react_runtime.contracts import Filter
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG, presentation_aliases, evidence_roots, validate_field_sources


OutputMode = Literal["freeform", "table_only", "prose_only"]
OUTPUT_MODE_DESCRIPTION = (
    "freeform允许所需解释与表；table_only仅交付要求的表及明确要求的日期/单位；"
    "prose_only直答存在性、计数或指标，保留对象、日期、来源与缺失，不加表、其他对象、"
    "原因、背景或伪装成正文的名单。查询对象和指标不自动成为展示列。"
)


class FieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: str = Field(min_length=1, max_length=120,
        description="用户明确要求的一个表格列，保留含义和顺序；独立汇总或总数不是列。")
    field: str | None = Field(default=None,
        description="Exact catalog field, or null when unknown or ambiguous. Never omit the requested item.")


class FieldResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["unrestricted", "resolved", "unresolved"] = "unrestricted"
    proposed_fields: list[str | None] = Field(default_factory=list, max_length=12)
    reason: str = "no_explicit_fields"


class RowSelection(BaseModel):
    """User-requested final table bound; not a business-tool query limit."""
    model_config = ConfigDict(extra="forbid")

    requested: str = Field(min_length=1, max_length=240,
        description="用户明确的行数或排名窗口，保留原意。")
    limit: int = Field(gt=0, strict=True,
        description="最终表格行数上限；完整小集合或真实部分结果可更少，不凑数。")
    offset: int = Field(default=0, ge=0, strict=True,
        description="从0计的排名窗口起点；第4至6名为offset=3,limit=3。行数合格不证明排名成员正确。")


ROW_SELECTION_DESCRIPTION = (
    "仅本轮明确的整表行数/排名窗口，否则null。前N取offset=0,limit=N；第m至n名取"
    "offset=m-1,limit=n-m+1。不从指标值、时间窗、板高、工具limit或未来偏好推导。"
    "每组N只、全部并列不是整表上限。"
)


class MetricRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    statistic: Literal["count", "sum", "mean", "min", "max"]
    field: str | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    scope: dict[str, str | int | bool] = Field(default_factory=dict, max_length=8,
        description="Explicit source dimensions, e.g. event_status=closed; never values or invented facts.")


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dates: tuple[date, date]
    metrics: list[MetricRequirement] = Field(min_length=1, max_length=12)
    change: bool = True

    @model_validator(mode="after")
    def unique_dimensions(self):
        if self.dates[0] == self.dates[1] or len({item.label for item in self.metrics}) != len(self.metrics):
            raise ValueError("Comparison dates and metric labels must be distinct")
        return self


class OutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: OutputMode = Field(default="freeform", description=OUTPUT_MODE_DESCRIPTION)
    field_requests: list[FieldRequest] = Field(default_factory=list, max_length=12)
    fields: list[str] = Field(default_factory=list, max_length=12,
        description="Backend-derived hard fields; also accepts legacy checkpoints without field_requests.")
    field_resolution: FieldResolution = Field(default_factory=FieldResolution)
    table_required: bool = Field(default=False,
        description="A table is a deliverable for THIS turn, not merely a preference for future answers.")
    row_selection: RowSelection | None = Field(default=None, description=ROW_SELECTION_DESCRIPTION)
    comparison: ComparisonRequest | None = None
    required_metadata: list[Literal["date", "units"]] = Field(default_factory=list, max_length=2)

    @property
    def unavailable_fields(self):
        """Unbound requested columns have identities, never invented evidence values."""
        return {field: item.requested
                for index, (field, item) in enumerate(zip(self.fields, self.field_requests), start=1)
                if field == f"unavailable_requested_{index}"}

    @model_validator(mode="before")
    @classmethod
    def read_previous_contract(cls, value):
        """Read saved conversations without reviving removed completion gates."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        value.pop("content_requirements", None)
        if isinstance(value.get("row_selection"), dict):
            value["row_selection"] = {key: item for key, item in value["row_selection"].items()
                                     if key not in {"sort_by", "descending"}}
        return value

    @model_validator(mode="after")
    def resolve_fields(self):
        if self.comparison is not None:
            self.fields = ["metric", "before", "after"] + (["change"] if self.comparison.change else [])
            self.field_resolution = FieldResolution(status="resolved", proposed_fields=self.fields,
                reason="typed_metric_comparison")
            self.table_required = True
            return self
        # Only actual requests or legacy fields can establish a hard constraint.
        # A model-supplied diagnostic is never authority to resolve fields.
        active = bool(self.field_requests or self.fields)
        proposed = ([item.field for item in self.field_requests] if self.field_requests
                    else list(self.fields) if self.fields else list(self.field_resolution.proposed_fields))
        if not proposed:
            reason, status = "no_explicit_fields", "unrestricted"
        elif any(field is None for field in proposed):
            reason, status = "unbound_requested_field", "unresolved"
        elif any(field not in DISPLAY_FIELD_CATALOG for field in proposed):
            reason, status = "unknown_catalog_field", "unresolved"
        elif len(set(proposed)) != len(proposed):
            reason, status = "duplicate_catalog_field", "unresolved"
        elif not active:
            reason, status = "saved_proposal_is_not_a_binding", "unresolved"
        else:
            reason, status = "all_requested_fields_bound", "resolved"
        if self.field_requests:
            # One unsupported column must not erase the remaining column contract.
            # Reserved identities are presentation slots, not provider row keys.
            seen = set()
            self.fields = []
            for index, item in enumerate(self.field_requests, start=1):
                field = item.field
                self.fields.append(field if field in DISPLAY_FIELD_CATALOG and field not in seen
                                   else f"unavailable_requested_{index}")
                seen.add(field)
        else:
            self.fields = list(proposed) if status == "resolved" else []
        self.field_resolution = FieldResolution(status=status, proposed_fields=proposed, reason=reason)
        return self


KnownDisplayField = Literal.__getitem__(tuple(DISPLAY_FIELD_CATALOG))


class ReviewFieldRequest(FieldRequest):
    field: KnownDisplayField | None = Field(default=None,
        description="Choose an exact field from display_field_catalog, or null. Chinese labels are requested text, never raw fields.")


class ReviewOutputContract(BaseModel):
    """LLM wire schema has one mapping list and no backend diagnostics/fields."""
    model_config = ConfigDict(extra="forbid")

    mode: OutputMode = Field(default="freeform", description=OUTPUT_MODE_DESCRIPTION)
    field_requests: list[ReviewFieldRequest] = Field(default_factory=list, max_length=12,
        description="仅按顺序保留用户明确列或适用的历史列偏好，无则[]；不为开放概览或主题日期设计列。独立计数、汇总和解释保留在current_task。未知列保留requested且field=null；映射不证明数据存在。")
    table_required: bool = Field(default=False,
        description="True only when the current turn requires a table; false for prose_only and future display preferences.")
    row_selection: RowSelection | None = Field(default=None, description=ROW_SELECTION_DESCRIPTION)
    comparison: ComparisonRequest | None = Field(default=None,
        description="两日期指标表保留每项指标、统计量、筛选及范围；实体名单使用null。")
    required_metadata: list[Literal["date", "units"]] = Field(default_factory=list, max_length=2,
        description="仅用户明确要求的日期/单位，从证据渲染，纯表也须保留。")


class TaskInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_task: str = Field(default="", max_length=2400,
        description="Self-contained current request, resolving only relevant inherited intent and current overrides; no historical market facts.")
    pending_slots: list[str] = Field(default_factory=list, max_length=8,
        description="Required USER input that remains unknown. Not a data lookup or unsupported tool. Never guess a missing comparison date or stock from page defaults.")
    output_contract: OutputContract = Field(default_factory=OutputContract)


def validate_delivery(final, interpretation, store=None):
    """Reject a draft; never silently remove model text or change its columns."""
    contract = interpretation.output_contract
    errors = []
    if interpretation.pending_slots and final.status not in {"clarify", "refuse"}:
        errors.append("Required user input is unresolved; clarify: " + ", ".join(interpretation.pending_slots))
    if contract.mode == "prose_only" and final.table is not None:
        errors.append("A prose-only answer cannot include an evidence table; deliver only the requested direct answer and necessary scope or missing-data clarification")
    if final.table is not None and contract.row_selection is not None:
        if store is None:
            raise ValueError("Table row-count validation requires the current evidence store")
        row_count = len(store.get(final.table.evidence_id)["rows"])
        limit = contract.row_selection.limit
        if row_count > limit:
            errors.append(
                f"Table row count {row_count} exceeds the requested maximum {limit}; "
                "select the requested subset with compute_result before submitting, without "
                "silently truncating the source or dropping other requested conditions"
            )
    if final.table is not None and contract.fields:
        actual = [column.field for column in final.table.columns]
        aliases = presentation_aliases(store.get(final.table.evidence_id), store) if store else {}
        matches = len(actual) == len(contract.fields) and all(
            field in {requested, aliases.get(requested, requested)}
            for field, requested in zip(actual, contract.fields)
        )
        if not matches:
            errors.append(f"Table fields must match the requested order exactly: {contract.fields}; got {actual}")
        for column in final.table.columns:
            label = contract.unavailable_fields.get(column.field)
            if label is not None and column.label != label[:40]:
                errors.append("Unavailable columns must preserve their original requested labels")
    if contract.table_required and final.status == "complete" and final.table is None:
        errors.append("The current task requires an evidence table; a prose summary cannot complete it")
    if contract.mode == "table_only" and final.status == "complete" and final.table is not None:
        if final.delivery != "table" and final.answer.strip() != "{{evidence_table}}":
            errors.append("A complete table-only answer must contain only {{evidence_table}}, without heading or prose")
    if final.table is not None:
        if not set(contract.required_metadata) <= set(final.table.metadata):
            errors.append("Table must declare every requested date/unit annotation")
        if store is not None:
            record = store.get(final.table.evidence_id)
            try:
                validate_field_sources([column.field for column in final.table.columns], record, store,
                    allow_missing=final.status == "partial" and bool(final.missing))
                required_source_fields = [field for field in contract.fields
                    if DISPLAY_FIELD_CATALOG.get(field, {}).get("source_bound")]
                if (any(row.get(field) is None for row in record["rows"] for field in required_source_fields)
                        and (final.status != "partial" or not final.missing)):
                    raise ValueError("Requested source-specific field is unavailable; use partial and name the missing field")
                if contract.comparison is not None:
                    validate_comparison(final, contract.comparison, record, store)
            except ValueError as error:
                errors.append(str(error))
    if errors:
        raise ValueError("\n".join(errors))
    # Partial/empty/clarify/refuse may explain real missing data or safety limits.
    # Their semantic truth remains subject to evidence/status and compliance gates.


def validate_comparison(final, requested, record, store):
    payload = record.get("payload", {})
    dates = [value.isoformat() for value in requested.dates]
    labels = [item.label for item in requested.metrics]
    if (record["tool"] != "compare_metrics" or payload.get("table_kind") != "metric_comparison"
            or payload.get("comparison") != {"dates": dates, "metrics": labels}
            or [row.get("metric") for row in record["rows"]] != labels):
        raise ValueError("Requested metric identities/dates require matching compare_metrics evidence, not entity rows")
    columns = {column.field: column.label for column in final.table.columns}
    if columns.get("before") != dates[0] or columns.get("after") != dates[1]:
        raise ValueError("Metric period column labels must equal their evidence dates")
    lineage = payload.get("metric_lineage", [])
    if len(lineage) != len(requested.metrics):
        raise ValueError("Metric recipes are missing")
    for requirement, recipe in zip(requested.metrics, lineage):
        for period in ("before", "after"):
            value = recipe[period]
            source = store.get(value["evidence_id"])
            if source["tool"] == "evidence_view" and requirement.scope.get("view") != source.get("arguments", {}).get("view"):
                raise ValueError(f"Metric view scope must be explicitly requested: {requirement.label}")
            statistic, field = value["operation"], value.get("field")
            if statistic == "scalar" and field in {"matched_count", "total_count", "unique_stock_count"}:
                statistic, field = "count", "symbol" if field == "unique_stock_count" else None
            if (statistic != requirement.statistic or field != requirement.field
                    or value.get("filters", []) != [item.model_dump() for item in requirement.filters]):
                raise ValueError(f"Metric recipe does not match requested statistic/filter: {requirement.label}")
            for root in evidence_roots(source, store):
                dimensions = {**root.get("arguments", {}), **(root.get("payload", {})
                    if isinstance(root.get("payload"), dict) else {}), "tool": root["tool"]}
                if source["tool"] == "evidence_view":
                    dimensions["view"] = source["arguments"]["view"]
                if any(dimensions.get(key) != expected for key, expected in requirement.scope.items()):
                    raise ValueError(f"Metric source scope does not match request: {requirement.label}")
    if payload.get("data_missing") and (final.status != "partial" or not final.missing):
        raise ValueError("Missing metric values require partial with the explicit gaps")

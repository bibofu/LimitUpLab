"""Semantic task interpretation and deterministic presentation validation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agents.react_runtime.contracts import UpdateTask
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG


OutputMode = Literal["freeform", "table_only", "prose_only"]
OUTPUT_MODE_DESCRIPTION = (
    "freeform permits the requested explanation and table; table_only delivers only requested table fields. "
    "prose_only directly answers the requested existence questions, counts, or set of metrics, with necessary "
    "object, date, source, uncertainty or missing-data clarification. No tables, other objects, "
    "unrequested reasons or background, including lists written as prose. Query subjects and metrics "
    "are not automatically requested display fields."
)
CONTENT_REQUIREMENTS_DESCRIPTION = (
    "Non-column deliverables explicitly requested this turn: counts, summary metrics, comparisons, "
    "explanations, or data-gap reporting. Preserve each requested meaning and scope as natural language, "
    "without a catalog field mapping. A list plus its total has row fields in field_requests and the total "
    "here. Keep unknown requested meanings here too; clarify genuinely ambiguous user input through "
    "pending_slots, and require evidence or an explicit limitation before claiming fulfillment. "
    "These are obligations, not claims that the answer is already verified."
)
ContentRequirement = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class FieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: str = Field(min_length=1, max_length=120,
        description="One explicitly requested table/list row column, preserving its meaning and order; not a separate summary or total.")
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
        description="The user's explicit row-count or rank-window requirement, preserving its meaning.")
    limit: int = Field(gt=0, strict=True,
        description="Maximum final table rows, not a required minimum. A complete smaller collection or a genuine partial result may contain fewer rows.")
    offset: int = Field(default=0, ge=0, strict=True,
        description="Zero-based start of the requested rank window: ranks 4 through 6 use offset=3, limit=3. The final evidence must prove this window through deterministic selection.")
    sort_by: str | None = Field(default=None, min_length=1,
        description="Exact evidence field explicitly requested as the ranking basis, otherwise null.")
    descending: bool | None = Field(default=None, strict=True,
        description="Explicit ranking direction: true for descending, false for ascending; null when not specified.")


ROW_SELECTION_DESCRIPTION = (
    "Explicit final-table row bound for THIS turn, otherwise null. Top N, at most N, or N rows use "
    "offset=0 and limit=N; ranks m through n use offset=m-1 and limit=n-m+1. Do not derive this "
    "from a metric value, time window, board height, tool query limit, or a preference only for future "
    "answers. Preserve an explicit ranking field and direction in sort_by/descending; leave unspecified "
    "ones null. Per-group limits and requests to include all ties are not a global table-row bound."
)


class OutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: OutputMode = Field(default="freeform", description=OUTPUT_MODE_DESCRIPTION)
    content_requirements: list[ContentRequirement] = Field(default_factory=list, max_length=12,
        description=CONTENT_REQUIREMENTS_DESCRIPTION)
    field_requests: list[FieldRequest] = Field(default_factory=list, max_length=12)
    fields: list[str] = Field(default_factory=list, max_length=12,
        description="Backend-derived hard fields; also accepts legacy checkpoints without field_requests.")
    field_resolution: FieldResolution = Field(default_factory=FieldResolution)
    table_required: bool = Field(default=False,
        description="A table is a deliverable for THIS turn, not merely a preference for future answers.")
    row_selection: RowSelection | None = Field(default=None, description=ROW_SELECTION_DESCRIPTION)

    @model_validator(mode="after")
    def resolve_fields(self):
        # Only actual requests or legacy fields can establish a hard constraint.
        # A model-supplied diagnostic is never authority to resolve fields.
        # Persist legacy column requests even when a field cannot be resolved.
        # Content requirements never reclassify or erase existing field bindings.
        if not self.field_requests and self.fields:
            self.field_requests = [FieldRequest(requested=field, field=field) for field in self.fields]
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
        self.fields = list(proposed) if status == "resolved" else []
        self.field_resolution = FieldResolution(status=status, proposed_fields=proposed, reason=reason)
        return self


KnownDisplayField = Literal.__getitem__(tuple(DISPLAY_FIELD_CATALOG))


class ReviewFieldRequest(FieldRequest):
    field: KnownDisplayField | None = Field(default=None,
        description="Choose an exact field from display_field_catalog, or null. Chinese labels are requested text, never raw fields.")


class ReviewOutputContract(BaseModel):
    """Separate content obligations from column mappings on the review wire."""
    model_config = ConfigDict(extra="forbid")

    mode: OutputMode = Field(default="freeform", description=OUTPUT_MODE_DESCRIPTION)
    content_requirements: list[ContentRequirement] = Field(default_factory=list, max_length=12,
        description=CONTENT_REQUIREMENTS_DESCRIPTION)
    field_requests: list[ReviewFieldRequest] = Field(default_factory=list, max_length=12,
        description="Only explicitly requested table/list row columns, in order. Put separate counts, summary metrics and explanations in content_requirements, even when a table is also requested. Direct prose answers normally leave this empty. Include unknown requested columns with field=null; never omit or move them to content_requirements to avoid a binding failure.")
    table_required: bool = Field(default=False,
        description="True only when the current turn requires a table; false for prose_only and future display preferences.")
    row_selection: RowSelection | None = Field(default=None, description=ROW_SELECTION_DESCRIPTION)


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
    if interpretation.pending_slots and final.status not in {"clarify", "refuse"}:
        raise ValueError("Required user input is unresolved; clarify: " + ", ".join(interpretation.pending_slots))
    if (contract.field_requests and contract.field_resolution.status == "unresolved"
            and final.status == "complete"):
        raise ValueError("Requested display fields remain unresolved; clarify their meaning or deliver a truthful partial answer")
    if contract.mode == "prose_only" and final.table is not None:
        raise ValueError("A prose-only answer cannot include an evidence table; deliver only the requested direct answer and necessary scope or missing-data clarification")
    if final.table is not None and contract.row_selection is not None:
        if store is None:
            raise ValueError("Table row-count validation requires the current evidence store")
        row_count = len(store.get(final.table.evidence_id)["rows"])
        limit = contract.row_selection.limit
        if row_count > limit:
            raise ValueError(
                f"Table row count {row_count} exceeds the requested maximum {limit}; "
                "select the requested subset with compute_result before submitting, without "
                "silently truncating the source or dropping other requested conditions"
            )
        selection = contract.row_selection
        if not store.row_selection_matches(final.table.evidence_id, offset=selection.offset,
                                           limit=selection.limit, sort_by=selection.sort_by,
                                           descending=selection.descending):
            raise ValueError("Table evidence does not prove the requested row window and ordering; select the requested subset with compute_result")
    # An unresolved item must not erase the constraints of the known requests.
    bound_fields = contract.fields or list(dict.fromkeys(
        item.field for item in contract.field_requests if item.field in DISPLAY_FIELD_CATALOG))
    if final.table is not None and bound_fields:
        actual = [column.field for column in final.table.columns]
        if actual != bound_fields:
            raise ValueError(f"Table fields must match the requested order exactly: {bound_fields}; got {actual}")
    if contract.table_required and final.status == "complete" and final.table is None:
        raise ValueError("The current task requires an evidence table; a prose summary cannot complete it")
    if contract.mode == "table_only" and final.status == "complete" and final.table is not None:
        if final.answer.strip() != "{{evidence_table}}":
            raise ValueError("A complete table-only answer must contain only {{evidence_table}}, without heading or prose")
    # Partial/empty/clarify/refuse may explain real missing data or safety limits.
    # Their semantic truth remains subject to evidence/status and compliance gates.


def _requirement_items(requirements):
    return UpdateTask(requirements=requirements).requirements if requirements else []


def validate_requirements(requirements, status, store=None, *, requires_current_evidence=True):
    """Validate recorded obligations, without inventing tasks from natural language."""
    items = _requirement_items(requirements)
    if status == "complete" and any(item.status != "satisfied" for item in items):
        raise ValueError("Cannot complete while recorded requirements remain pending or unavailable")
    for item in items:
        if item.status != "satisfied":
            continue
        if requires_current_evidence and not item.evidence_ids:
            raise ValueError(f"Satisfied requirement {item.id} requires current usable evidence")
        for evidence_id in item.evidence_ids:
            if store is None:
                raise ValueError("Requirement evidence validation requires the current evidence store")
            record = store.get(evidence_id)
            if (store.scope_of(record) != "current_run"
                    or record.get("result_state") not in {"ok", "empty", "partial"}):
                raise ValueError(f"Satisfied requirement {item.id} must reference usable current-run evidence")


def update_requirements(current, proposed, store=None, *, requires_current_evidence=True):
    """Return a validated replacement without mutating the previous checklist."""
    old_items = _requirement_items(current)
    new_items = UpdateTask(requirements=proposed).requirements
    by_id = {item.id: item for item in new_items}
    for old in old_items:
        updated = by_id.get(old.id)
        if updated is None:
            raise ValueError("Cannot remove previously recorded requirements")
        if (updated.description, updated.source_text) != (old.description, old.source_text):
            raise ValueError(f"Cannot rewrite the description or source_text of requirement {old.id}")
    validate_requirements(new_items, None, store, requires_current_evidence=requires_current_evidence)
    return [item.model_dump(mode="json") for item in new_items]

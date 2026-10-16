"""Semantic task interpretation and deterministic presentation validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG, presentation_aliases


OutputMode = Literal["freeform", "table_only", "prose_only"]
OUTPUT_MODE_DESCRIPTION = (
    "freeform permits the requested explanation and table; table_only delivers only requested table fields. "
    "prose_only directly answers the requested existence questions, counts, or metrics, with necessary "
    "object, date, source, uncertainty or missing-data clarification. No tables, other objects, "
    "unrequested reasons or background, including lists written as prose. Query subjects and metrics "
    "are not automatically requested display fields."
)


class FieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: str = Field(min_length=1, max_length=120,
        description="One explicitly requested table/list column, preserving its meaning and order; not a separate summary or total.")
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
        description="Zero-based start of the requested rank window: ranks 4 through 6 use offset=3, limit=3. Retains intent; a row-count check alone does not prove rank membership.")


ROW_SELECTION_DESCRIPTION = (
    "Explicit final-table row bound for THIS turn, otherwise null. Top N, at most N, or N rows use "
    "offset=0 and limit=N; ranks m through n use offset=m-1 and limit=n-m+1. Do not derive this "
    "from a metric value, time window, board height, tool query limit, or a preference only for future "
    "answers. Per-group limits and requests to include all ties are not a global table-row bound."
)


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
        description="Explicit table/list columns in order. Keep separate counts, summary metrics and explanations in current_task, not here. Include unknown requested columns with field=null; mappings guide presentation and never prove tool data availability.")
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
    if final.table is not None and contract.fields:
        actual = [column.field for column in final.table.columns]
        aliases = presentation_aliases(store.get(final.table.evidence_id), store) if store else {}
        matches = len(actual) == len(contract.fields) and all(
            field in {requested, aliases.get(requested, requested)}
            for field, requested in zip(actual, contract.fields)
        )
        if not matches:
            raise ValueError(f"Table fields must match the requested order exactly: {contract.fields}; got {actual}")
        for column in final.table.columns:
            label = contract.unavailable_fields.get(column.field)
            if label is not None and column.label != label[:40]:
                raise ValueError("Unavailable columns must preserve their original requested labels")
    if contract.table_required and final.status == "complete" and final.table is None:
        raise ValueError("The current task requires an evidence table; a prose summary cannot complete it")
    if contract.mode == "table_only" and final.status == "complete" and final.table is not None:
        if final.answer.strip() != "{{evidence_table}}":
            raise ValueError("A complete table-only answer must contain only {{evidence_table}}, without heading or prose")
    # Partial/empty/clarify/refuse may explain real missing data or safety limits.
    # Their semantic truth remains subject to evidence/status and compliance gates.

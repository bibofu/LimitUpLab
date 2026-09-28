"""Semantic task interpretation and deterministic presentation validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["freeform", "table_only"] = "freeform"
    fields: list[str] = Field(default_factory=list, max_length=12,
        description="Exact evidence fields, in requested order, only for explicit field restrictions. Leave empty if the tool field names are uncertain; preserve the full request in current_task.")
    table_required: bool = Field(default=False,
        description="A table is a deliverable for THIS turn, not merely a preference for future answers.")


class TaskInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_task: str = Field(default="", max_length=2400,
        description="Self-contained current request, resolving only relevant inherited intent and current overrides; no historical market facts.")
    pending_slots: list[str] = Field(default_factory=list, max_length=8,
        description="Required USER input that remains unknown. Not a data lookup or unsupported tool. Never guess a missing comparison date or stock from page defaults.")
    output_contract: OutputContract = Field(default_factory=OutputContract)


def validate_delivery(final, interpretation):
    """Reject a draft; never silently remove model text or change its columns."""
    contract = interpretation.output_contract
    if interpretation.pending_slots and final.status not in {"clarify", "refuse"}:
        raise ValueError("Required user input is unresolved; clarify: " + ", ".join(interpretation.pending_slots))
    if final.table is not None and contract.fields:
        actual = [column.field for column in final.table.columns]
        if actual != contract.fields:
            raise ValueError(f"Table fields must match the requested order exactly: {contract.fields}; got {actual}")
    if contract.table_required and final.status == "complete" and final.table is None:
        raise ValueError("The current task requires an evidence table; a prose summary cannot complete it")
    if contract.mode == "table_only" and final.status == "complete" and final.table is not None:
        if final.answer.strip() != "{{evidence_table}}":
            raise ValueError("A complete table-only answer must contain only {{evidence_table}}, without heading or prose")
    # Partial/empty/clarify/refuse may explain real missing data or safety limits.
    # Their semantic truth remains subject to evidence/status and compliance gates.

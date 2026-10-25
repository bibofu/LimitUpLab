"""A durable pause is control flow, never a draft or a delivered answer."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PausedRunOutcome:
    run_id: str
    checkpoint_id: str | None
    pause_revision: int
    resume_mode: Literal["interrupt", "checkpoint", "delivery"]
    interrupt_id: str | None = None


class PausedRunError(Exception):
    """Propagate a confirmed checkpoint through the synchronous response adapter."""

    def __init__(self, outcome: PausedRunOutcome):
        super().__init__("Run paused at a durable boundary")
        self.outcome = outcome

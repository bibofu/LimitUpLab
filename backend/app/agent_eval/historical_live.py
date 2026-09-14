"""Bounded real local-tool execution, with baseline drift checked before LLM use."""

from contextlib import contextmanager

from app.agent_eval.core_batch import LocalResearchRegistry
from app.agent_eval.local_capture import read_local_session
from app.agents.query_contract import query_reference_date_override
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway


class HistoricalDataDrift(ValueError):
    pass


class HistoricalLiveRegistry(LocalResearchRegistry):
    def __init__(self, database, baseline):
        events = []
        for day in baseline.trading_calendar.trading_dates:
            anchor = baseline.anchor_datetime.replace(year=day.year, month=day.month, day=day.day)
            _, _, loaded = read_local_session(database, anchor)
            events.extend(loaded)
        super().__init__(events)
        self.reference_date = baseline.anchor_datetime.date()
        self.baseline_checks = []
        # Execute real production tools, never execute_frozen_calls or lookup by arguments.
        gateway = ToolGateway(self, EvidenceStore())
        with self.anchored():
            for recording in baseline.recordings:
                args = gateway.validate({"name":recording.tool,"args":recording.arguments})
                _, payload, state = gateway.execute(recording.tool, args)
                if payload != recording.observation.payload or state != recording.observation.state:
                    raise HistoricalDataDrift("historical tool output differs from versioned baseline")
                self.baseline_checks.append(recording.id)
        self.attempts = []  # No frozen matching takes place in this registry.

    @contextmanager
    def anchored(self):
        with query_reference_date_override(self.reference_date):
            yield self

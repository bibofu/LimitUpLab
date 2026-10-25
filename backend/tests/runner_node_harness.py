"""Explicit test adapters: pure business nodes and real durable public calls.

NodeRunner is intentionally NOT a runtime integration test. Durable checkpoint,
ownership, tool receipts and recovery are exercised by the PostgreSQL suites.
"""

from contextlib import contextmanager
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.run_lifecycle import CURRENT_CONTROL, RunControl, RunLifecycle
from app.agents.runner.result import RunnerResult
from app.agents.runner.runner import AgentRunner
from app.repositories import ChatSessionRepository
from app.services.durable_runtime_host import DurableRuntimeHost


class NodeRunner(AgentRunner):
    """Drive isolated decision/tool nodes with scripted, nonpersistent tools."""

    def execute(self, call):
        return self._execute(call)

    def run_nodes(self):
        state = {"messages": [SystemMessage(content=self.system_prompt),
                              HumanMessage(content=self.request.message)],
                 "pending": [], "tool_calls": [], "tool_statuses": [], "tool_messages": [],
                 "model_rounds": 0, "tool_count": 0, "done": False}
        for _ in range(40):
            state.update(self.tools(state) if state["pending"] else self.agent(state))
            if state.get("done"):
                return RunnerResult(
                    answer=state.get("answer", ""), status=state.get("status", "error"),
                    stop_reason=state.get("stop_reason", "unknown"), tool_calls=state["tool_calls"],
                    tool_messages=state["tool_messages"], model_rounds=state["model_rounds"],
                    tool_count=state["tool_count"], error=state.get("error"),
                    compliance_reviewed=state.get("compliance_reviewed", False),
                )
        raise AssertionError("Business nodes did not terminate within the test budget")


@contextmanager
def durable_request(request):
    """Provide the real owner, lease, host and saver to a public adapter call."""
    owner = "adapter-owner-" + uuid4().hex
    request = request.model_copy(update={"session_id": "adapter-session-" + uuid4().hex})
    lifecycle = RunLifecycle()
    ChatSessionRepository().ensure_session(request.session_id, owner_id=owner)
    row, _ = lifecycle.create(request, owner)
    lease = lifecycle.claim(row["run_id"])
    control = RunControl(lifecycle, row, lease)
    host = DurableRuntimeHost(lifecycle.database_url).start()
    token = CURRENT_CONTROL.set(control)
    try:
        yield request
    finally:
        CURRENT_CONTROL.reset(token)
        host.close()
        lease.release()

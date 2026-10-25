"""Synthetic provenance and observations for long-term memory regressions.

Every value in this world is a fixture, not a statement about a real security.
The run/session records are persisted in the isolated PostgreSQL test schema.
"""

from dataclasses import dataclass, field
from uuid import uuid4

from app.models import AgentChatRequest, AgentChatResponse, AgentToolTrace
from app.repositories import ChatSessionRepository
from app.services.agent_run_services import AgentRunServices


OLD_DATE = "2026-09-01"
NEW_DATE = "2026-09-02"
SOURCE = "fixture://long-term-memory/v1"
OLD_ARCHIVE = "历史样本甲的研究记录；仅适用于2026-09-01，不代表本轮行情。"
CURRENT_OBSERVATION = {
    "status": "ok", "trade_date": NEW_DATE, "source": SOURCE + "/current",
    "rows": [{"symbol": "600001", "name": "样本甲", "close": 11.0}],
    "row_count": 1,
}


@dataclass
class LongTermToolWorld:
    database_url: str
    owner: str = field(default_factory=lambda: "owner-" + uuid4().hex)
    other_owner: str = field(default_factory=lambda: "owner-" + uuid4().hex)

    def __post_init__(self):
        self.chats = ChatSessionRepository(self.database_url)
        self.runs = AgentRunServices(self.database_url)

    def source_run(self, message="请长期记住：回答简洁。", *, owner=None, committed=True):
        owner = owner or self.owner
        session = self.chats.create_session(owner_id=owner)
        request = AgentChatRequest(session_id=session.session_id, message_id="fixture-message",
                                   message=message, trade_date=OLD_DATE)
        row, fresh = self.runs.registry.create(request, owner)
        assert fresh
        if committed:
            response = AgentChatResponse(
                session_id=session.session_id, run_id=row["run_id"], intent="react_research",
                answer=OLD_ARCHIVE, task_status="complete", generated_by="fixed-tool-world",
                references=[SOURCE + "/historical"],
                tool_calls=["fixture_history"],
                tool_results=[AgentToolTrace(name="fixture_history", summary="历史固定样本", output={
                    "status": "ok", "trade_date": OLD_DATE, "source": SOURCE + "/historical",
                    "rows": [{"symbol": "600001", "name": "样本甲", "close": 10.0}],
                })],
            )
            self.runs.delivery.commit(row["run_id"], owner, response.model_dump(mode="json"))
        return {"run_id": row["run_id"], "session_id": session.session_id,
                "owner_id": owner, "message": message}

    def current_tool(self, symbol="600001"):
        assert symbol == "600001", "this fixed world has one synthetic current quote"
        return {**CURRENT_OBSERVATION, "rows": [dict(CURRENT_OBSERVATION["rows"][0])]}

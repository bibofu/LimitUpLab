"""Small fixed tool world shared by persistence and process-restart tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.services.durable_checkpointer import FencedPostgresSaver, psycopg_url


class FixtureState(TypedDict, total=False):
    observations: list[dict]
    answer: str


def run_async(coroutine):
    # psycopg's async client needs a selector event loop on Windows.
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)


@asynccontextmanager
async def native_saver(database_url):
    async with AsyncConnectionPool(
        psycopg_url(database_url), min_size=1, max_size=2, open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    ) as pool:
        await pool.wait(timeout=5)
        yield FencedPostgresSaver(pool)


def execution_config(row, lease):
    return {"configurable": {
        "thread_id": row["session_id"], "checkpoint_ns": "",
        "run_id": row["run_id"], "owner_id": row["owner_id"],
        "lease_token": lease.token, "fence": lease.fence,
    }}


def fixture_graph(saver, query, answer):
    graph = StateGraph(FixtureState)
    graph.add_node("query", query)
    graph.add_node("answer", answer)
    graph.add_edge(START, "query")
    graph.add_edge("query", "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=saver)


async def subprocess_worker():
    """Stop after a genuine committed super-step until the test kills us."""
    ready = Path(os.environ["DURABLE_FIXTURE_READY"])
    calls = ready.with_suffix(".calls")

    async def query(_state):
        calls.write_text("1", encoding="utf-8")
        return {"observations": [{"symbol": "600001", "source": "fixture://frozen"}]}

    async def answer(_state):
        # durability=sync persisted the previous node before scheduling this one.
        ready.write_text("checkpoint-committed", encoding="utf-8")
        await asyncio.Event().wait()

    async with native_saver(os.environ["DURABLE_FIXTURE_URL"]) as saver:
        graph = fixture_graph(saver, query, answer)
        await graph.ainvoke({}, json.loads(os.environ["DURABLE_FIXTURE_CONFIG"]), durability="sync")


if __name__ == "__main__":
    run_async(subprocess_worker())

"""Zero-LLM readiness captures for ten local Live candidates, on an isolated copy.

These captures are NOT Live Agent runs or approved Golden cases. Use a dedicated
process: network/subprocess access is denied while production tools execute.
"""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.agent_eval.core_batch import write_json
from app.agent_eval.recorder import capture_tool, save_capture
from app.agents.tools import AgentToolRegistry
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository


def live_requests(day, symbol):
    """One shared real Gateway path; no per-tool execution adapters."""
    return [
        ("daily_board_promotion", {"days": 1, "end_date": day}, "报告当日连板晋级样本数、晋级数和观察比例，不作预测。"),
        ("stock_kline", {"symbol": symbol, "days": 20, "end_date": day}, "报告最近20根日K的最新收盘价和数据日期，说明缺失。"),
        ("post_limit_screen", {"shape": "high_drawdown", "data_as_of": day, "limit": 5}, "筛选涨停后高位回撤，报告名单和可评价覆盖率。"),
        ("post_limit_path", {"symbol": symbol, "data_as_of": day, "anchor_date": day}, "报告该股涨停锚点及后续路径，不能把锚点当天当次日表现。"),
        ("post_limit_statistics", {"data_as_of": day, "statistics_days": 7, "shapes": ["high_drawdown"]}, "报告近7个交易日完整样本数和样本质量，不推断未来收益。"),
        ("first_board_ratings", {"trade_date": day, "symbols": [symbol]}, "报告该股首板评级、分数、置信度和快照来源，不输出交易指令。"),
        ("first_board_filter", {"query": symbol, "trade_date": day}, "在首板评级中查找该股，只列代码名称和分数；无记录须说明。"),
        ("first_board_critic", {"symbol": symbol, "trade_date": day}, "报告该股原置信度、质疑后建议置信度和缺失证据。"),
        ("prediction_quality_audit", {"start_date": day, "end_date": day, "top_k": 10}, "报告预测结果覆盖率及缺失，不把覆盖率当胜率。"),
        ("scoring_policy_status", {}, "报告采集时的Champion与Challenger状态，不激活策略，不冒充历史时点状态。"),
    ]


@contextmanager
def isolated_io(snapshot):
    """Permit SQLite only inside the explicit copy; deny remote fallbacks."""
    original = sqlite3.connect
    allowed = snapshot.resolve()

    def connect(database, *args, **kwargs):
        readonly_uri = str(database) == allowed.as_uri() + "?mode=ro" and kwargs.get("uri") is True
        plain_path = not str(database).startswith("file:") and Path(database).resolve() == allowed
        if not (readonly_uri or plain_path):
            raise PermissionError("Live readiness may access only its snapshot")
        connection = original(database, *args, **kwargs)
        connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY
                                  if action == sqlite3.SQLITE_ATTACH else sqlite3.SQLITE_OK)
        return connection

    def denied(*args, **kwargs):
        raise PermissionError("Live readiness forbids network and subprocess fallback")

    with patch.object(sqlite3, "connect", connect), \
         patch.object(socket.socket, "connect", denied), \
         patch.object(socket, "create_connection", denied), \
         patch.object(socket.socket, "connect_ex", denied), \
         patch.object(socket, "getaddrinfo", denied), \
         patch.object(socket.socket, "sendto", denied), \
         patch.object(subprocess, "Popen", denied):
        yield


class NoRemoteCollector:
    def __getattr__(self, name):
        raise PermissionError("remote collector disabled for local readiness")


def prepare(database: Path, destination: Path, anchor: datetime):
    if anchor.utcoffset() is None:
        raise ValueError("timezone-aware anchor required")
    anchor = anchor.astimezone(ZoneInfo("Asia/Shanghai"))
    database = database.resolve(strict=True)
    destination.mkdir(parents=True, exist_ok=False)
    snapshot = destination / "source-snapshot.sqlite"
    # SQLite backup includes WAL content, unlike copying the main database file.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source, \
         closing(sqlite3.connect(snapshot)) as target:
        source.backup(target)
    day = anchor.date().isoformat()
    with isolated_io(snapshot):
        with closing(sqlite3.connect(snapshot)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM limit_up_events WHERE trade_date <= ? ORDER BY trade_date, symbol", (day,)).fetchall()
            covered_symbols = {row[0] for row in connection.execute(
                "SELECT symbol FROM stock_daily_bars WHERE trade_date <= ? GROUP BY symbol "
                "HAVING COUNT(*) >= 20 AND MAX(trade_date) = ?", (day, day))}
        repository = SQLiteLimitUpRepository(snapshot, seed_if_empty=False)
        events = [repository._event_from_row(row) for row in rows]
        candidates = [event for event in events if event.trade_date.isoformat() == day
                      and event.board_height == 1 and event.symbol in covered_symbols]
        if not candidates:
            raise ValueError("no first-board event with 20 local bars through requested date")
        symbol = candidates[0].symbol
        registry = AgentToolRegistry(events, SQLiteFirstBoardRepository(snapshot), NoRemoteCollector(), profile="extended")
        entries = []
        for index, (tool, arguments, task) in enumerate(live_requests(day, symbol), 1):
            entry = {"id": f"LH-B{index:03}", "tool": tool, "arguments": arguments,
                     "question": f"日期{day}，标的{symbol}。{task}", "status": "candidate"}
            try:
                artifact = capture_tool(registry, tool=tool, arguments=arguments,
                    anchor_datetime=anchor, recording_id=entry["id"],
                    provenance="production Gateway on local SQLite backup; not an as-known-at-date archive",
                    source_manifest={"snapshot": snapshot.name, "event_count": len(events),
                                     "source_date": day, "network_allowed": False})
                capture = entry["id"] + ".capture.json"
                save_capture(artifact, destination / capture)
                state = artifact.body.recording.observation.state
                entry.update(capture=capture, checksum=artifact.checksum, observation_state=state,
                             readiness="blocked" if state == "error" else "captured")
            except Exception as exc:
                entry.update(readiness="blocked", error=f"{type(exc).__name__}: {exc}")
            entries.append(entry)
            # Persist progress after every tool so an interrupted batch is inspectable.
            write_json(destination / f"progress-{index:02}.json", {"cases": entries, "complete": False})
    report = {"schema_version": "basic70-live-readiness-v1", "cases": entries,
              "captured": sum(e["readiness"] == "captured" for e in entries),
              "blocked": sum(e["readiness"] == "blocked" for e in entries),
              "model_calls": 0, "new_active_golden": 0, "live_agent_runs": 0,
              "release_eligible": False,
              "limitations": ["Readiness captures only; case assertions and Live worker adapter still required.",
                              "Snapshot may contain later revisions; not point-in-time historical truth.",
                              "Tools may initialize or update only the disposable copy."]}
    write_json(destination / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--anchor", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    report = prepare(args.database, args.output_dir, args.anchor)
    print(json.dumps({key: report[key] for key in ("captured", "blocked", "model_calls")}, ensure_ascii=False))

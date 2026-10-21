"""Verify a single-instance PostgreSQL cutover without changing research records.

Run after the final SQLite import and before resuming market-data writers. Chat,
cache and runtime tables may legitimately grow after cutover and are excluded
from the frozen business-data comparison. Optional write probes use unique IDs
and remove only their own rows, including when a probe fails.
"""

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.database import begin_write, connect, connect_readonly, initialize_database, is_postgres
from app.models import ChatSessionMessage
from app.repositories.agent_cache_repository import AgentCacheRepository
from app.repositories.chat_session_repository import ChatSessionRepository
from app.repositories.first_board_repository import FirstBoardRepository
from app.repositories.limit_up_repository import LimitUpRepository
from app.repositories.scoring_policy_repository import ScoringPolicyRepository
from app.storage.migration import target_fingerprint
from app.storage.postgres import get_engine
from app.storage.schema import HEAD_REVISION, assert_schema_current, metadata


BUSINESS_TABLES = (
    "limit_up_events", "first_board_features", "stock_daily_bars", "stock_intraday_bars",
    "first_board_outcomes", "first_board_enrichment_snapshots", "agent_predictions",
    "agent_live_prediction_snapshots", "prediction_snapshot_archive",
    "recommendation_prediction_finals", "daily_review_snapshots", "scoring_policies",
    "scoring_policy_runs", "prediction_time_audits",
)


class VerificationError(ValueError):
    """A known failed check; messages contain no connection details or row data."""


def require(condition, check):
    if not condition:
        raise VerificationError(check)


def verify_business_data(migration_report):
    require(migration_report.get("status") == "verified_and_committed", "migration_report_status")
    require(migration_report.get("target_revision") == HEAD_REVISION, "migration_report_revision")
    expected_tables = migration_report.get("tables", {})
    for name in BUSINESS_TABLES:
        expected = expected_tables.get(name, {})
        require(expected.get("status") == "verified" and "sha256" in expected,
                "migration_report_table:" + name)
    results = {}
    engine = get_engine()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            assert_schema_current(connection)
            for name in BUSINESS_TABLES:
                actual = target_fingerprint(connection, metadata.tables[name])
                expected = expected_tables[name]
                require(actual == {key: expected[key] for key in ("rows", "sha256")},
                        "business_fingerprint:" + name)
                results[name] = {**actual, "status": "matched"}
    return results


def verify_repository_reads(database_url=None):
    """Exercise model conversion and application query contracts on real rows."""
    first_board = FirstBoardRepository(database_url)
    results = {}
    with closing(connect_readonly(database_url)) as db:
        latest = db.execute("SELECT MAX(trade_date) FROM limit_up_events").fetchone()[0]
        require(latest is not None, "limit_up_events_empty")
        expected = db.execute("SELECT symbol FROM limit_up_events WHERE trade_date=?", (latest,)).fetchall()
        events = LimitUpRepository(database_url, seed_if_empty=False).list_latest_events()
        require(sorted(item.symbol for item in events) == sorted(row[0] for row in expected),
                "latest_limit_up_events")
        require(all(item.trade_date.isoformat() == latest for item in events), "latest_event_dates")
        results["limit_up_events"] = {"trade_date": latest, "rows": len(events)}

        feature_date = db.execute("SELECT MAX(trade_date) FROM first_board_features").fetchone()[0]
        require(feature_date is not None, "first_board_features_empty")
        features = first_board.list_features_for_date(feature_date)
        expected = db.execute("SELECT symbol FROM first_board_features WHERE trade_date=?", (feature_date,)).fetchall()
        require(sorted(item.symbol for item in features) == sorted(row[0] for row in expected), "first_board_features")
        results["first_board_features"] = {"trade_date": feature_date, "rows": len(features)}

        symbol = db.execute("SELECT symbol FROM stock_daily_bars ORDER BY trade_date DESC,symbol LIMIT 1").fetchone()
        require(symbol is not None, "stock_daily_bars_empty")
        bars = first_board.list_daily_bars(symbol[0])
        expected = db.execute("SELECT COUNT(*) FROM stock_daily_bars WHERE symbol=?", (symbol[0],)).fetchone()[0]
        require(len(bars) == expected and bool(bars), "daily_bars")
        require([item.trade_date for item in bars] == sorted(item.trade_date for item in bars), "daily_bar_order")
        results["stock_daily_bars"] = {"symbol": symbol[0], "rows": len(bars)}

        intraday = db.execute("SELECT symbol,trade_date,period_minutes FROM stock_intraday_bars "
                              "ORDER BY trade_date DESC,symbol,period_minutes LIMIT 1").fetchone()
        require(intraday is not None, "stock_intraday_bars_empty")
        bars = first_board.list_intraday_bars(symbol=intraday[0], trade_date=date.fromisoformat(intraday[1]),
                                             period_minutes=intraday[2])
        expected = db.execute("SELECT COUNT(*) FROM stock_intraday_bars WHERE symbol=? AND trade_date=? "
                              "AND period_minutes=?", tuple(intraday)).fetchone()[0]
        require(len(bars) == expected and bool(bars), "intraday_bars")
        results["stock_intraday_bars"] = {"symbol": intraday[0], "trade_date": intraday[1], "rows": len(bars)}

        prediction_date = db.execute("SELECT MAX(trade_date) FROM agent_predictions").fetchone()[0]
        require(prediction_date is not None, "agent_predictions_empty")
        parsed_date = date.fromisoformat(prediction_date)
        predictions = first_board.list_predictions_between(parsed_date, parsed_date)
        expected = db.execute("SELECT prediction_id FROM agent_predictions WHERE trade_date=?", (prediction_date,)).fetchall()
        require(sorted(item.prediction_id for item in predictions) == sorted(row[0] for row in expected), "predictions")
        results["agent_predictions"] = {"trade_date": prediction_date, "rows": len(predictions)}

        outcome = db.execute("SELECT symbol,base_trade_date FROM first_board_outcomes "
                             "ORDER BY base_trade_date DESC,symbol LIMIT 1").fetchone()
        require(outcome is not None, "first_board_outcomes_empty")
        result = first_board.get_outcome(outcome[0], date.fromisoformat(outcome[1]))
        require(result is not None and result.symbol == outcome[0], "historical_outcome")
        results["first_board_outcomes"] = {"symbol": outcome[0], "base_trade_date": outcome[1]}

        snapshot = db.execute("SELECT MAX(trade_date) FROM agent_live_prediction_snapshots").fetchone()[0]
        if snapshot:
            result = first_board.get_live_prediction_snapshot(date.fromisoformat(snapshot))
            require(result is not None and result.trade_date.isoformat() == snapshot, "live_snapshot_read")
        results["live_snapshot"] = {"trade_date": snapshot, "status": "read" if snapshot else "no_source_records"}

        policies = ScoringPolicyRepository(database_url)
        champion = policies.get_champion()
        expected = db.execute("SELECT version FROM scoring_policies WHERE status='champion'").fetchall()
        require(len(expected) == 1 and champion is not None and champion.version == expected[0][0], "champion_policy")
        require(policies.get_policy(champion.version) == champion, "policy_lookup")
        results["champion_policy"] = {"version": champion.version}
    return results


def verify_write_paths(database_url=None):
    """Commit, reread, update, delete and roll back only UUID-scoped probe data."""
    identity = "database-verification-" + uuid4().hex
    owner = identity + "-owner"
    cache_key = identity + "-cache"
    rollback_key = identity + "-rollback"
    chats = ChatSessionRepository(database_url)
    cache = AgentCacheRepository(database_url)
    now = datetime.now(timezone.utc)
    try:
        chats.create_session(session_id=identity, owner_id=owner, title="Database verification")
        chats.append_message(ChatSessionMessage(
            session_id=identity, message_id=identity + "-message", role="user",
            content="Database persistence verification; no research output.", created_at=now,
        ), owner_id=owner)
        independent = ChatSessionRepository(database_url)
        detail = independent.get_session(identity, owner_id=owner)
        require(detail is not None and detail.message_count == 1, "committed_session_read")
        independent.rename_session(identity, "Verified database persistence", owner_id=owner)
        require(chats.get_session(identity, owner_id=owner).title == "Verified database persistence", "session_update")
        require(chats.delete_session(identity, owner_id=owner), "session_delete")
        require(independent.get_session(identity, owner_id=owner) is None, "session_delete_visible")

        for revision in (1, 2):
            cache.set_json(cache_key=cache_key, scope="database-verification", payload={"revision": revision},
                           expires_at=now + timedelta(minutes=5))
            require(AgentCacheRepository(database_url).get_json(cache_key) == {"revision": revision},
                    "cache_upsert_read")
        cache.set_json(cache_key=cache_key, scope="database-verification", payload={},
                       expires_at=now - timedelta(seconds=1))
        require(cache.get_json(cache_key) is None, "cache_expiry_delete")

        with closing(connect(database_url)) as db:
            initialize_database(db)
            begin_write(db)
            db.execute("INSERT INTO agent_cache (cache_key,scope,payload_json,expires_at,created_at) "
                       "VALUES (?,?,?,?,?)", (rollback_key, "database-verification", "{}", now.isoformat(), now.isoformat()))
            db.rollback()
        with closing(connect_readonly(database_url)) as db:
            require(db.execute("SELECT COUNT(*) FROM agent_cache WHERE cache_key=?", (rollback_key,)).fetchone()[0] == 0,
                    "transaction_rollback")
        return {"status": "passed", "session_crud": True, "cache_upsert_expiry": True, "rollback": True,
                "probe_id": identity, "cleanup": "verified"}
    finally:
        # Never run broad expiry or delete queries: only rows belonging to this probe.
        chats.delete_session(identity, owner_id=owner)
        with closing(connect(database_url)) as db:
            db.execute("DELETE FROM agent_cache WHERE cache_key IN (?,?)", (cache_key, rollback_key))
            db.commit()
        with closing(connect_readonly(database_url)) as db:
            require(db.execute("SELECT COUNT(*) FROM chat_sessions WHERE session_id=?", (identity,)).fetchone()[0] == 0,
                    "probe_session_cleanup")
            require(db.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (identity,)).fetchone()[0] == 0,
                    "probe_message_cleanup")
            require(db.execute("SELECT COUNT(*) FROM agent_cache WHERE cache_key IN (?,?)", (cache_key, rollback_key)).fetchone()[0] == 0,
                    "probe_cache_cleanup")


def verify_local_database(migration_report, *, check_writes=False):
    with closing(connect_readonly()) as db:
        require(is_postgres(db), "configured_database_must_be_postgresql")
        initialize_database(db)
    result = {"status": "passed", "database_backend": "postgresql", "schema_revision": HEAD_REVISION,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "business_tables": verify_business_data(migration_report),
              "repository_reads": verify_repository_reads()}
    result["writes"] = verify_write_paths() if check_writes else {"status": "not_requested"}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--check-writes", action="store_true", help="Create and clean up uniquely named session/cache probes")
    args = parser.parse_args(argv)
    configure_runtime_environment()
    try:
        migration_report = json.loads(args.migration_report.read_text(encoding="utf-8"))
        report = verify_local_database(migration_report, check_writes=args.check_writes)
    except Exception as error:
        # Driver messages, tracebacks and DSNs may contain credentials or private rows.
        report = {"status": "failed", "error_type": type(error).__name__}
        if isinstance(error, VerificationError):
            report["check"] = str(error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

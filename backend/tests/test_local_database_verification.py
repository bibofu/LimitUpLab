"""Cutover verification must detect loss and never delete unrelated user data."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from app.database import connect, connect_readonly, initialize_database
from app.models import AgentPrediction, FirstBoardOutcome, StockDailyBar, StockIntradayKLineBar
from app.repositories.agent_cache_repository import SQLiteAgentCacheRepository
from app.repositories.chat_session_repository import SQLiteChatSessionRepository
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.repositories.scoring_policy_repository import SQLiteScoringPolicyRepository
from app.services.first_board_features import build_first_board_features
from app.services.sample_data import SAMPLE_EVENTS
from app.services.scoring_policy import build_default_scoring_policy
from app.storage.migration import target_fingerprint
from app.storage.postgres import get_engine
from app.storage.schema import HEAD_REVISION, metadata


spec = importlib.util.spec_from_file_location(
    "verify_local_database", Path(__file__).resolve().parents[1] / "scripts/verify_local_database.py")
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


@pytest.fixture(params=["sqlite", "postgresql"])
def storage(request, tmp_path):
    if request.param == "postgresql":
        request.getfixturevalue("postgresql_database")
        return None
    path = tmp_path / "verification.sqlite"
    with closing(connect(path)) as db:
        initialize_database(db)
    return path


def keep_user_rows(storage):
    chats = SQLiteChatSessionRepository(storage)
    chats.create_session(session_id="user-session", title="Keep this research")
    cache = SQLiteAgentCacheRepository(storage)
    cache.set_json(cache_key="user-cache", scope="research", payload={"keep": True},
                   expires_at=datetime.now(timezone.utc) + timedelta(hours=1))


def assert_only_user_rows_remain(storage):
    with closing(connect_readonly(storage)) as db:
        assert [row[0] for row in db.execute("SELECT session_id FROM chat_sessions")] == ["user-session"]
        assert [row[0] for row in db.execute("SELECT cache_key FROM agent_cache")] == ["user-cache"]
        assert db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM agent_predictions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM scoring_policies").fetchone()[0] == 0


def test_committed_probe_crud_and_rollback_leave_no_probe_rows(storage):
    keep_user_rows(storage)
    first = verification.verify_write_paths(storage)
    second = verification.verify_write_paths(storage)
    assert first["status"] == "passed"
    assert first["cleanup"] == "verified"
    assert first["probe_id"] != second["probe_id"]
    assert_only_user_rows_remain(storage)


def test_probe_failure_cleans_up_only_its_own_rows(storage, monkeypatch):
    keep_user_rows(storage)
    original = SQLiteAgentCacheRepository.set_json

    def fail_after_commit(self, **kwargs):
        original(self, **kwargs)
        raise RuntimeError("probe failed after its cache write")

    monkeypatch.setattr(SQLiteAgentCacheRepository, "set_json", fail_after_commit)
    with pytest.raises(RuntimeError, match="probe failed"):
        verification.verify_write_paths(storage)
    assert_only_user_rows_remain(storage)


def test_empty_business_database_cannot_pass_repository_validation(storage):
    with pytest.raises(verification.VerificationError, match="limit_up_events_empty"):
        verification.verify_repository_reads(storage)


def test_repository_reads_decode_all_required_business_models(storage):
    event = SAMPLE_EVENTS[0].model_copy(update={"board_height": 1})
    now = datetime.now(timezone.utc)
    SQLiteLimitUpRepository(storage).upsert_events([event])
    repo = SQLiteFirstBoardRepository(storage)
    repo.upsert_features(build_first_board_features([event]))
    repo.upsert_daily_bars([StockDailyBar(
        symbol=event.symbol, trade_date=event.trade_date, open=10, high=11, low=9, close=10.5,
        volume=100, amount=1000, source="synthetic-test", created_at=now,
    )])
    repo.replace_intraday_bars(symbol=event.symbol, trade_date=event.trade_date, period_minutes=5,
                              source="synthetic-test", bars=[StockIntradayKLineBar(
                                  timestamp=now, open=10, high=11, low=9, close=10.5, volume=100, amount=1000)])
    repo.upsert_outcomes([FirstBoardOutcome(
        base_trade_date=event.trade_date, symbol=event.symbol, promoted_to_second_board=False,
        outcome_ready=False, outcome_version="synthetic-test", created_at=now,
    )])
    repo.upsert_predictions([AgentPrediction(
        prediction_id="synthetic-test", trade_date=event.trade_date, symbol=event.symbol, name=event.name,
        score=80, rating="A", confidence=0.8, scoring_version="synthetic-test", prediction_source="historical_backtest",
        data_as_of=event.trade_date, facts_json={}, reasons=[], risks=[], created_at=now,
    )])
    SQLiteScoringPolicyRepository(storage).upsert_policy(build_default_scoring_policy())
    result = verification.verify_repository_reads(storage)
    assert result["agent_predictions"]["rows"] == 1
    assert result["stock_intraday_bars"]["rows"] == 1
    assert result["first_board_features"]["rows"] == 1
    assert result["live_snapshot"]["status"] == "no_source_records"


@pytest.fixture
def baseline_report(postgresql_database):
    SQLiteLimitUpRepository().upsert_events(SAMPLE_EVENTS[:2])
    with get_engine().connect() as db:
        tables = {name: {**target_fingerprint(db, metadata.tables[name]), "status": "verified"}
                  for name in verification.BUSINESS_TABLES}
    return {"status": "verified_and_committed", "target_revision": HEAD_REVISION, "tables": tables}


def test_business_fingerprints_ignore_new_sessions_but_reject_data_loss(baseline_report):
    SQLiteChatSessionRepository().create_session(session_id="new-after-cutover")
    assert verification.verify_business_data(baseline_report)["limit_up_events"]["rows"] == 2
    with closing(connect()) as db:
        db.execute("DELETE FROM limit_up_events WHERE symbol=?", (SAMPLE_EVENTS[0].symbol,))
        db.commit()
    with pytest.raises(verification.VerificationError, match="business_fingerprint:limit_up_events"):
        verification.verify_business_data(baseline_report)


def test_business_fingerprints_detect_changes_without_row_count_change(baseline_report):
    with closing(connect()) as db:
        db.execute("UPDATE limit_up_events SET name=? WHERE symbol=?", ("changed value", SAMPLE_EVENTS[0].symbol))
        db.commit()
    with pytest.raises(verification.VerificationError, match="business_fingerprint:limit_up_events"):
        verification.verify_business_data(baseline_report)


def test_incomplete_report_rejected_before_connecting():
    with pytest.raises(verification.VerificationError, match="migration_report_table"):
        verification.verify_business_data({"status": "verified_and_committed", "target_revision": HEAD_REVISION})


def test_cli_failure_report_never_echoes_driver_credentials(tmp_path, monkeypatch):
    source = tmp_path / "migration.json"
    source.write_text("{}", encoding="utf-8")
    report = tmp_path / "result.json"
    monkeypatch.setattr(verification, "configure_runtime_environment", lambda: None)

    def failed(*args, **kwargs):
        raise RuntimeError("postgresql://username:private-password@host/database private row data")

    monkeypatch.setattr(verification, "verify_local_database", failed)
    assert verification.main(["--migration-report", str(source), "--report", str(report)]) == 1
    assert json.loads(report.read_text()) == {"status": "failed", "error_type": "RuntimeError"}


def test_cutover_verifier_rejects_accidental_sqlite_selection(tmp_path, monkeypatch):
    path = tmp_path / "retained.sqlite"
    with closing(connect(path)) as db:
        initialize_database(db)
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(path))
    with pytest.raises(verification.VerificationError, match="configured_database_must_be_postgresql"):
        verification.verify_local_database({})

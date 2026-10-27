"""Memory health report after all migrations."""

from app.database import connect, initialize_database
from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from scripts.check_memory_health import collect_health


def test_clean_memory_schema_reports_healthy(postgresql_database):
    with connect() as db:
        initialize_database(db)
        report = collect_health(db)
    assert report["ok"] is True
    assert report["critical_issues"] == 0
    assert report["checks"]["duplicate_active_identity"] == 0
    assert report["checks"]["candidates_missing_ttl"] == 0


def test_health_reports_embedding_coverage_and_staleness(postgresql_database):
    repository = LongTermMemoryRepository()
    owner = "visitor_" + "a5" * 16
    saved = repository.put(owner, MemoryWrite(
        memory_type="research_context", key="research:financials",
        value="财务报告研究背景", source_user_message="记录财报研究背景",
    ), "health-embedding-put")
    with connect() as db:
        initialize_database(db)
        assert collect_health(db)["checks"]["active_missing_embedding"] == 1

    vector = [0.0] * 512
    vector[0] = 1.0
    assert repository.save_embedding(
        owner, saved.current.memory_id, vector, "test-model", "stale-source",
    )
    with connect() as db:
        initialize_database(db)
        checks = collect_health(db)["checks"]
    assert checks["active_missing_embedding"] == 0
    assert checks["active_embedding_model_mismatch"] == 1
    assert checks["active_embedding_source_mismatch"] == 1

"""Memory health report after all migrations."""

from app.database import connect, initialize_database
from scripts.check_memory_health import collect_health


def test_clean_memory_schema_reports_healthy(postgresql_database):
    with connect() as db:
        initialize_database(db)
        report = collect_health(db)
    assert report["ok"] is True
    assert report["critical_issues"] == 0
    assert report["checks"]["duplicate_active_identity"] == 0
    assert report["checks"]["candidates_missing_ttl"] == 0

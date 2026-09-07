import sqlite3
from pathlib import Path

from scripts.retire_first_board_discovery import retire_discovery


def test_cleanup_is_backed_up_idempotent_and_preserves_prediction_records(tmp_path: Path) -> None:
    database = tmp_path / "test.sqlite"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as connection:
        for table in (
            "agent_live_prediction_snapshots",
            "agent_predictions",
            "first_board_outcomes",
            "daily_review_snapshots",
            "scoring_policies",
        ):
            connection.execute(f"CREATE TABLE {table} (value TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('protected')")
        connection.execute("CREATE TABLE first_board_discovery_snapshots (value TEXT)")
        connection.execute("INSERT INTO first_board_discovery_snapshots VALUES ('retired')")
        for table in (
            "recommendation_intelligence_changes",
            "recommendation_intelligence_current",
            "recommendation_intelligence_snapshots",
            "recommendation_prediction_finals",
        ):
            connection.execute(f"CREATE TABLE {table} (value TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('mixed')")

    first_backup, counts = retire_discovery(database, backup_dir)
    second_backup, second_counts = retire_discovery(database, backup_dir)

    assert first_backup.exists()
    assert second_backup.exists()
    assert counts == second_counts == {
        "agent_live_prediction_snapshots": 1,
        "agent_predictions": 1,
        "first_board_outcomes": 1,
        "daily_review_snapshots": 1,
        "scoring_policies": 1,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='first_board_discovery_snapshots'"
        ).fetchone() is None
        for table in (
            "recommendation_intelligence_changes",
            "recommendation_intelligence_current",
            "recommendation_intelligence_snapshots",
            "recommendation_prediction_finals",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

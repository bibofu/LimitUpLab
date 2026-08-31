"""SQLite persistence for immutable first-board discovery snapshots."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.database import connect, initialize_database
from app.models import FirstBoardDiscoveryResponse


class SQLiteFirstBoardDiscoveryRepository:
    """Store and retrieve one strategy snapshot per market data date."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save(
        self,
        response: FirstBoardDiscoveryResponse,
        *,
        replace: bool = False,
    ) -> bool:
        """Insert an immutable response and report whether a row was created."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            before = connection.total_changes
            if replace:
                connection.execute(
                    """
                    DELETE FROM first_board_discovery_snapshots
                    WHERE data_as_of = ? AND strategy_version = ?
                    """,
                    (response.data_as_of.isoformat(), response.generated_by),
                )
            connection.execute(
                """
                INSERT INTO first_board_discovery_snapshots (
                    data_as_of, strategy_version, target_trade_date,
                    response_json, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(data_as_of, strategy_version) DO NOTHING
                """,
                (
                    response.data_as_of.isoformat(),
                    response.generated_by,
                    response.target_trade_date.isoformat()
                    if response.target_trade_date
                    else None,
                    response.model_dump_json(),
                    response.source,
                    response.snapshot_created_at.isoformat(),
                ),
            )
            connection.commit()
            return connection.total_changes > before
        finally:
            connection.close()

    def get(
        self,
        data_as_of: date,
        strategy_version: str | None = None,
    ) -> FirstBoardDiscoveryResponse | None:
        """Return one date's newest matching strategy snapshot."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            sql = """
                SELECT response_json
                FROM first_board_discovery_snapshots
                WHERE data_as_of = ?
            """
            parameters: list[object] = [data_as_of.isoformat()]
            if strategy_version:
                sql += " AND strategy_version = ?"
                parameters.append(strategy_version)
            sql += " ORDER BY created_at DESC LIMIT 1"
            row = connection.execute(sql, parameters).fetchone()
        finally:
            connection.close()
        return (
            FirstBoardDiscoveryResponse.model_validate_json(row["response_json"])
            if row
            else None
        )

    def get_latest(self) -> FirstBoardDiscoveryResponse | None:
        """Return the latest persisted discovery snapshot."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT response_json
                FROM first_board_discovery_snapshots
                ORDER BY data_as_of DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        return (
            FirstBoardDiscoveryResponse.model_validate_json(row["response_json"])
            if row
            else None
        )

    def list_by_target_date(
        self,
        start_date: date,
        end_date: date,
    ) -> list[FirstBoardDiscoveryResponse]:
        """Return immutable snapshots whose intended session is in the range."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT response_json
                FROM first_board_discovery_snapshots
                WHERE target_trade_date BETWEEN ? AND ?
                ORDER BY target_trade_date, created_at
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        finally:
            connection.close()
        return [
            FirstBoardDiscoveryResponse.model_validate_json(row["response_json"])
            for row in rows
        ]

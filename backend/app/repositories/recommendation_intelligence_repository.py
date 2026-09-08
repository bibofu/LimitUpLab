"""SQLite persistence for half-hour recommendation intelligence snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.database import connect, initialize_database
from app.models import RecommendationIntelligenceResponse
from app.services.prediction_time import validate_final_response


RETIRED_DISCOVERY_WARNING = "历史快照中的低位挖掘条目已退役，不在当前视图展示；原始记录保留。"


def _load_persisted_response(raw_json: str) -> RecommendationIntelligenceResponse:
    """Adapt known retired items on read; keep public validation and stored originals strict."""

    payload = json.loads(raw_json)
    items = payload.get("items", [])
    if any(isinstance(item, dict) and item.get("strategy") == "discovery" for item in items):
        payload["items"] = [
            item for item in items
            if not (isinstance(item, dict) and item.get("strategy") == "discovery")
        ]
        payload["warnings"] = list(dict.fromkeys([*payload.get("warnings", []), RETIRED_DISCOVERY_WARNING]))
    return RecommendationIntelligenceResponse.model_validate(payload)


class SQLiteRecommendationIntelligenceRepository:
    """Store recent mutable evidence without rewriting prediction snapshots."""

    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save(
        self,
        response: RecommendationIntelligenceResponse,
    ) -> None:
        """Replace the current draft and append only material field changes."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            current_row = connection.execute(
                "SELECT response_json FROM recommendation_intelligence_current WHERE slot = 1"
            ).fetchone()
            previous = (
                _load_persisted_response(
                    current_row["response_json"]
                )
                if current_row
                else None
            )
            if previous is not None and (
                (previous.items and not response.items)
                or RETIRED_DISCOVERY_WARNING in previous.warnings
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO recommendation_intelligence_snapshots (
                        refresh_id, refreshed_at, status, response_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        previous.refresh_id,
                        previous.refreshed_at.isoformat(),
                        previous.status,
                        current_row["response_json"],
                    ),
                )
            connection.executemany(
                """
                INSERT INTO recommendation_intelligence_changes (
                    refresh_id, changed_at, strategy, base_trade_date,
                    symbol, changes_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                self._change_records(previous, response),
            )
            connection.execute(
                """
                INSERT INTO recommendation_intelligence_current (
                    slot, refresh_id, refreshed_at, status, response_json
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    refresh_id = excluded.refresh_id,
                    refreshed_at = excluded.refreshed_at,
                    status = excluded.status,
                    response_json = excluded.response_json
                """,
                (
                    response.refresh_id,
                    response.refreshed_at.isoformat(),
                    response.status,
                    response.model_dump_json(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_latest(self) -> RecommendationIntelligenceResponse | None:
        """Return the newest persisted refresh."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT response_json
                FROM recommendation_intelligence_current
                WHERE slot = 1
                """
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT response_json
                    FROM recommendation_intelligence_snapshots
                    ORDER BY refreshed_at DESC
                    LIMIT 1
                    """
                ).fetchone()
        finally:
            connection.close()
        return (
            _load_persisted_response(
                row["response_json"]
            )
            if row
            else None
        )

    def get_latest_displayable(self) -> RecommendationIntelligenceResponse | None:
        """Return the latest refresh containing candidates, with history fallback."""

        current = self.get_latest()
        if current is not None and current.items:
            return current

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT response_json, snapshot_at
                FROM (
                    SELECT response_json, refreshed_at AS snapshot_at
                    FROM recommendation_intelligence_snapshots
                    UNION ALL
                    SELECT response_json, finalized_at AS snapshot_at
                    FROM recommendation_prediction_finals
                )
                ORDER BY snapshot_at DESC
                """
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            response = _load_persisted_response(
                row["response_json"]
            )
            if response.items:
                return response
        return current

    def save_final(self, response: RecommendationIntelligenceResponse) -> bool:
        """Persist one immutable pre-open final and expose it as current."""

        validate_final_response(response)
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO recommendation_prediction_finals (
                    target_trade_date, finalized_at, response_json
                ) VALUES (?, ?, ?)
                """,
                (
                    response.target_trade_date.isoformat(),
                    (response.finalized_at or response.refreshed_at).isoformat(),
                    response.model_dump_json(),
                ),
            )
            connection.commit()
            inserted = cursor.rowcount > 0
        finally:
            connection.close()
        if inserted:
            self.save(response)
        return inserted

    def get_final(
        self,
        target_trade_date: str,
    ) -> RecommendationIntelligenceResponse | None:
        """Return the immutable prediction finalized for one target trading day."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT response_json
                FROM recommendation_prediction_finals
                WHERE target_trade_date = ?
                """,
                (target_trade_date,),
            ).fetchone()
        finally:
            connection.close()
        return (
            _load_persisted_response(
                row["response_json"]
            )
            if row
            else None
        )

    def list_changes(
        self,
        *,
        strategy: str,
        base_trade_date: str,
        symbol: str,
    ) -> list[dict[str, Any]]:
        """Return recorded field changes for one candidate lifecycle."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT refresh_id, changed_at, changes_json
                FROM recommendation_intelligence_changes
                WHERE strategy = ? AND base_trade_date = ? AND symbol = ?
                ORDER BY changed_at ASC, change_id ASC
                """,
                (strategy, base_trade_date, symbol),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "refresh_id": row["refresh_id"],
                "changed_at": row["changed_at"],
                "changes": json.loads(row["changes_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _change_records(
        previous: RecommendationIntelligenceResponse | None,
        current: RecommendationIntelligenceResponse,
    ) -> list[tuple[str, str, str, str, str, str]]:
        """Build compact records only for user-visible candidate changes."""

        if previous is None:
            return []
        previous_items = {
            (item.strategy, item.base_trade_date, item.symbol): item
            for item in previous.items
        }
        tracked_fields = (
            "rank",
            "draft_score",
            "dragon_tiger_on_list",
            "dragon_tiger_net_buy_amount",
            "popularity_rank",
            "popularity_rank_change",
            "latest_news",
            "financial_report",
            "data_missing",
        )
        records: list[tuple[str, str, str, str, str, str]] = []
        for item in current.items:
            key = (item.strategy, item.base_trade_date, item.symbol)
            old = previous_items.get(key)
            if old is None:
                continue
            changes: dict[str, dict[str, Any]] = {}
            old_payload = old.model_dump(mode="json")
            new_payload = item.model_dump(mode="json")
            for field in tracked_fields:
                if old_payload.get(field) != new_payload.get(field):
                    changes[field] = {
                        "old": old_payload.get(field),
                        "new": new_payload.get(field),
                    }
            if changes:
                records.append(
                    (
                        current.refresh_id,
                        current.refreshed_at.isoformat(),
                        item.strategy,
                        item.base_trade_date.isoformat(),
                        item.symbol,
                        json.dumps(changes, ensure_ascii=False, sort_keys=True),
                    )
                )
        return records

"""Immutable strategy runs and exact-trading-day outcome persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.database import connect, initialize_database
from app.strategy_models import StrategyRunSnapshot


class SQLiteStrategyRepository:
    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path

    def save_if_absent(self, snapshot: StrategyRunSnapshot) -> StrategyRunSnapshot:
        """Freeze one strategy/date/version result without later replacement."""

        candidates = list(snapshot.payload.get("candidates") or [])
        for candidate in candidates:
            anchor = candidate.get("anchor_date") or candidate.get("trade_date")
            if not anchor or str(anchor) > snapshot.signal_date.isoformat():
                raise ValueError("Every strategy candidate requires a non-future limit-up anchor.")
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            existing = connection.execute(
                "SELECT payload_json FROM strategy_runs WHERE strategy_id=? AND strategy_version=? AND signal_date=?",
                (snapshot.strategy_id, snapshot.strategy_version, snapshot.signal_date.isoformat()),
            ).fetchone()
            if existing is not None:
                return StrategyRunSnapshot.model_validate_json(existing["payload_json"])
            encoded = snapshot.model_dump_json()
            connection.execute(
                """
                INSERT INTO strategy_runs (
                    run_id,strategy_id,strategy_version,signal_date,data_as_of,
                    generated_at,input_fingerprint,status,maturity,output_type,
                    candidate_count,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot.run_id, snapshot.strategy_id, snapshot.strategy_version,
                    snapshot.signal_date.isoformat(), snapshot.data_as_of.isoformat(),
                    snapshot.generated_at.isoformat(), snapshot.input_fingerprint,
                    snapshot.status, snapshot.maturity, snapshot.output_type,
                    snapshot.candidate_count, encoded,
                ),
            )
            connection.executemany(
                "INSERT INTO strategy_candidates (run_id,symbol,anchor_date,candidate_json) VALUES (?,?,?,?)",
                [
                    (
                        snapshot.run_id,
                        str(item["symbol"]),
                        str(item.get("anchor_date") or item.get("trade_date")),
                        json.dumps(item, ensure_ascii=False, sort_keys=True),
                    )
                    for item in candidates
                ],
            )
            connection.commit()
            return snapshot
        finally:
            connection.close()

    def latest(self, strategy_id: str) -> StrategyRunSnapshot | None:
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                "SELECT payload_json FROM strategy_runs WHERE strategy_id=? ORDER BY signal_date DESC LIMIT 1",
                (strategy_id,),
            ).fetchone()
        finally:
            connection.close()
        return StrategyRunSnapshot.model_validate_json(row["payload_json"]) if row else None

    def list_runs(self, strategy_id: str, limit: int = 30) -> list[StrategyRunSnapshot]:
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                "SELECT payload_json FROM strategy_runs WHERE strategy_id=? ORDER BY signal_date DESC LIMIT ?",
                (strategy_id, max(1, min(limit, 120))),
            ).fetchall()
        finally:
            connection.close()
        return [StrategyRunSnapshot.model_validate_json(row["payload_json"]) for row in rows]

    def get_candidate(self, run_id: str, symbol: str) -> dict | None:
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                "SELECT candidate_json FROM strategy_candidates WHERE run_id=? AND symbol=?",
                (run_id, symbol),
            ).fetchone()
        finally:
            connection.close()
        return json.loads(row["candidate_json"]) if row else None

    def backfill_outcomes(self) -> int:
        """Fill only exact D+1/D+3/D+5 dates; absent bars remain not ready."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            calendar = [row[0] for row in connection.execute(
                "SELECT DISTINCT trade_date FROM stock_daily_bars ORDER BY trade_date"
            )]
            calendar_index = {day: index for index, day in enumerate(calendar)}
            rows = connection.execute(
                """
                SELECT r.run_id,r.signal_date,c.symbol
                FROM strategy_runs r JOIN strategy_candidates c ON c.run_id=r.run_id
                """
            ).fetchall()
            updated = 0
            for row in rows:
                index = calendar_index.get(row["signal_date"])
                if index is None:
                    continue
                required = {offset: calendar[index + offset] for offset in (1, 3, 5) if index + offset < len(calendar)}
                if not required:
                    continue
                bars = {
                    bar["trade_date"]: bar
                    for bar in connection.execute(
                        "SELECT trade_date,open,high,low,close FROM stock_daily_bars WHERE symbol=? AND trade_date>? AND trade_date<=?",
                        (row["symbol"], row["signal_date"], max(required.values())),
                    )
                }
                first = bars.get(required.get(1, ""))
                if first is None or float(first["open"]) <= 0:
                    continue
                entry = float(first["open"])
                def close_return(offset: int) -> float | None:
                    bar = bars.get(required.get(offset, ""))
                    return round((float(bar["close"]) / entry - 1) * 100, 4) if bar else None
                observed = [bars[day] for day in required.values() if day in bars]
                d5_window = [bars.get(calendar[index + offset]) for offset in range(1, 6) if index + offset < len(calendar)]
                d5_ready = len(d5_window) == 5 and all(d5_window)
                mae5 = round((min(float(bar["low"]) for bar in d5_window) / entry - 1) * 100, 4) if d5_ready else None
                mfe5 = round((max(float(bar["high"]) for bar in d5_window) / entry - 1) * 100, 4) if d5_ready else None
                values = (
                    row["run_id"], row["symbol"], int(1 in required and required[1] in bars),
                    int(3 in required and required[3] in bars), int(d5_ready),
                    close_return(1), close_return(3), close_return(5), mae5, mfe5,
                    datetime.now(timezone.utc).isoformat(),
                )
                connection.execute(
                    """
                    INSERT INTO strategy_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id,symbol) DO UPDATE SET
                        d1_ready=excluded.d1_ready,d3_ready=excluded.d3_ready,d5_ready=excluded.d5_ready,
                        d1_open_to_close_pct=excluded.d1_open_to_close_pct,
                        d3_open_to_close_pct=excluded.d3_open_to_close_pct,
                        d5_open_to_close_pct=excluded.d5_open_to_close_pct,
                        mae5_pct=excluded.mae5_pct,mfe5_pct=excluded.mfe5_pct,updated_at=excluded.updated_at
                    """,
                    values,
                )
                updated += 1
            connection.commit()
            return updated
        finally:
            connection.close()

"""SQLite registry for versioned scoring policies and optimization runs."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from app.database import connect, initialize_database
from app.models import ScoringPolicy, ScoringPolicyOptimizationResponse
from app.services.scoring_policy import (
    LEGACY_DEFAULT_POLICY_VERSIONS,
    build_default_scoring_policy,
)


class SQLiteScoringPolicyRepository:
    """Persist Champion/Challenger policies independently from predictions."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path

    def ensure_default_policy(self) -> ScoringPolicy:
        """Register the code-compatible baseline when the registry is empty."""

        champion = self.get_champion()
        if champion is not None and not (
            champion.source == "default"
            and champion.version in LEGACY_DEFAULT_POLICY_VERSIONS
        ):
            return champion
        policy = build_default_scoring_policy()
        self.upsert_policy(policy)
        if champion is None:
            return policy
        return self.promote_policy(
            policy.version,
            activated_at=policy.activated_at,
        )

    def upsert_policy(self, policy: ScoringPolicy) -> None:
        """Insert or update one immutable-version policy record."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO scoring_policies (
                    version, parent_version, status, factor_weights_json,
                    source, rationale_json, training_start_date,
                    training_end_date, created_at, activated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version) DO UPDATE SET
                    parent_version = excluded.parent_version,
                    status = excluded.status,
                    factor_weights_json = excluded.factor_weights_json,
                    source = excluded.source,
                    rationale_json = excluded.rationale_json,
                    training_start_date = excluded.training_start_date,
                    training_end_date = excluded.training_end_date,
                    activated_at = excluded.activated_at
                """,
                self._policy_record(policy),
            )
            connection.commit()
        finally:
            connection.close()

    def get_champion(self) -> ScoringPolicy | None:
        """Return the active scoring policy, if one has been registered."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT * FROM scoring_policies
                WHERE status = 'champion'
                ORDER BY activated_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        return self._policy_from_row(row) if row else None

    def get_policy(self, version: str) -> ScoringPolicy | None:
        """Return one policy by version."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                "SELECT * FROM scoring_policies WHERE version = ?",
                (version,),
            ).fetchone()
        finally:
            connection.close()
        return self._policy_from_row(row) if row else None

    def list_policies(self, limit: int = 20) -> list[ScoringPolicy]:
        """Return Champion first, then recent challengers and archived versions."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT * FROM scoring_policies
                ORDER BY
                    CASE status WHEN 'champion' THEN 0 WHEN 'challenger' THEN 1 ELSE 2 END,
                    created_at DESC
                LIMIT ?
                """,
                (max(limit, 1),),
            ).fetchall()
        finally:
            connection.close()
        return [self._policy_from_row(row) for row in rows]

    def promote_policy(
        self,
        version: str,
        *,
        activated_at: datetime | None = None,
    ) -> ScoringPolicy:
        """Atomically archive the current Champion and activate a Challenger."""

        timestamp = activated_at or datetime.now(timezone.utc)
        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM scoring_policies WHERE version = ?",
                (version,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown scoring policy: {version}")
            connection.execute(
                "UPDATE scoring_policies SET status = 'archived' WHERE status = 'champion'"
            )
            connection.execute(
                """
                UPDATE scoring_policies
                SET status = 'champion', activated_at = ?
                WHERE version = ?
                """,
                (timestamp.isoformat(), version),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        promoted = self.get_policy(version)
        if promoted is None:
            raise RuntimeError(f"Promoted policy disappeared: {version}")
        return promoted

    def save_optimization_run(self, report: ScoringPolicyOptimizationResponse) -> None:
        """Persist the complete reproducible optimization report."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO scoring_policy_runs (
                    run_id, champion_version, challenger_version,
                    promotion_eligible, activated, report_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    promotion_eligible = excluded.promotion_eligible,
                    activated = excluded.activated,
                    report_json = excluded.report_json
                """,
                (
                    report.run_id,
                    report.champion_policy.version,
                    report.challenger_policy.version,
                    int(report.comparison.promotion_eligible),
                    int(report.activated),
                    report.model_dump_json(),
                    report.challenger_policy.created_at.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_latest_optimization_run(self) -> ScoringPolicyOptimizationResponse | None:
        """Return the most recently persisted optimization report."""

        connection = connect(self.database_path)
        try:
            initialize_database(connection)
            row = connection.execute(
                """
                SELECT report_json FROM scoring_policy_runs
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return ScoringPolicyOptimizationResponse.model_validate_json(row["report_json"])

    @staticmethod
    def _policy_record(policy: ScoringPolicy) -> tuple[object, ...]:
        return (
            policy.version,
            policy.parent_version,
            policy.status,
            json.dumps(policy.factor_weights, ensure_ascii=False, sort_keys=True),
            policy.source,
            json.dumps(policy.rationale, ensure_ascii=False),
            _date_value(policy.training_start_date),
            _date_value(policy.training_end_date),
            policy.created_at.isoformat(),
            policy.activated_at.isoformat() if policy.activated_at else None,
        )

    @staticmethod
    def _policy_from_row(row) -> ScoringPolicy:
        return ScoringPolicy(
            version=row["version"],
            parent_version=row["parent_version"],
            status=row["status"],
            factor_weights=json.loads(row["factor_weights_json"]),
            source=row["source"],
            rationale=json.loads(row["rationale_json"]),
            training_start_date=(
                date.fromisoformat(row["training_start_date"])
                if row["training_start_date"]
                else None
            ),
            training_end_date=(
                date.fromisoformat(row["training_end_date"])
                if row["training_end_date"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            activated_at=(
                datetime.fromisoformat(row["activated_at"])
                if row["activated_at"]
                else None
            ),
        )


def _date_value(value: date | None) -> str | None:
    return value.isoformat() if value else None

"""Orchestration for immutable post-limit strategy results."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from app.agents.first_board import build_first_board_ratings
from app.post_limit_query_contract import PostLimitQueryContract
from app.repositories import (
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteStrategyRepository,
    get_limit_up_repository,
)
from app.repositories.post_limit_repository import load_post_limit_dataset
from app.services.post_limit import build_post_limit_path, build_post_limit_screen, build_post_limit_statistics
from app.services.relay_universe import is_relay_candidate_symbol
from app.services.strategy_catalog import (
    STRATEGY_IDS,
    get_strategy_definition,
    list_strategy_definitions,
    strategy_shape,
)
from app.strategy_models import StrategyCatalogResponse, StrategyRunSnapshot


def build_strategy_catalog(repository: SQLiteStrategyRepository | None = None) -> StrategyCatalogResponse:
    repo = repository or SQLiteStrategyRepository()
    definitions = []
    for definition in list_strategy_definitions():
        latest = repo.latest(definition.strategy_id)
        definitions.append(definition.model_copy(update={
            "latest_data_date": latest.data_as_of if latest else None,
            "latest_sample_size": latest.candidate_count if latest else 0,
        }))
    return StrategyCatalogResponse(strategies=definitions, generated_at=datetime.now(timezone.utc))


def materialize_strategy_run(
    strategy_id: str,
    *,
    data_as_of: date | None = None,
    repository: SQLiteStrategyRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
) -> StrategyRunSnapshot:
    """Build and freeze the first result for one strategy/date/version."""

    if strategy_id not in STRATEGY_IDS:
        raise KeyError(strategy_id)
    repo = repository or SQLiteStrategyRepository()
    definition = get_strategy_definition(strategy_id)
    if strategy_id == "relay_one_to_two":
        events = (limit_up_repository or get_limit_up_repository()).list_events()
        if not events:
            raise LookupError("No local limit-up events available.")
        signal_date = data_as_of or max(item.trade_date for item in events)
        first_repo = first_board_repository or SQLiteFirstBoardRepository()
        ratings = build_first_board_ratings(
            events=events,
            trade_date=signal_date,
            first_board_repository=first_repo,
        )
        candidates = []
        for rank, item in enumerate(
            (item for item in ratings.candidates if is_relay_candidate_symbol(item.facts.symbol)),
            start=1,
        ):
            if rank > 10:
                break
            candidates.append({
                "symbol": item.facts.symbol,
                "name": item.facts.name,
                "anchor_date": item.facts.trade_date.isoformat(),
                "signal_date": signal_date.isoformat(),
                "rank": rank,
                "score": item.score,
                "rating": item.rating,
                "confidence": item.confidence,
                "industry": item.facts.industry,
                "concept": item.facts.concept,
                "reasons": item.reasons,
                "risks": item.risks,
                "data_missing": item.facts.data_missing,
            })
        payload: dict[str, Any] = {
            "status": "ready" if candidates else "empty",
            "snapshot_kind": "immutable_ranked_research",
            "data_as_of": signal_date.isoformat(),
            "candidate_count": len(candidates),
            "candidates": candidates,
            "warnings": ["研究排名仍处于前向验证阶段，不代表确定性预测。"],
        }
        version = ratings.generated_by
    else:
        shape = strategy_shape(strategy_id)
        dataset = load_post_limit_dataset(data_as_of, database_path=repo.database_path)
        resolved_date = data_as_of or dataset.latest_data_date
        if resolved_date is None:
            raise LookupError("No completed local daily bars available.")
        contract = PostLimitQueryContract(
            mode="screen", shape=shape, shapes=(shape,), data_as_of=resolved_date,
            recent_limit_days=5, limit=100, exhaustive=True,
        )
        payload = build_post_limit_screen(dataset, contract)
        signal_date = resolved_date
        version = str(payload.get("rule_version") or definition.version)
    fingerprint = _fingerprint(payload)
    snapshot = StrategyRunSnapshot(
        run_id=f"{strategy_id}:{version}:{signal_date.isoformat()}",
        strategy_id=strategy_id,
        strategy_version=version,
        signal_date=signal_date,
        data_as_of=signal_date,
        generated_at=datetime.now(timezone.utc),
        input_fingerprint=fingerprint,
        status=payload.get("status", "data_missing"),
        maturity=definition.maturity,
        output_type=definition.output_type,
        candidate_count=len(payload.get("candidates") or []),
        payload=payload,
    )
    return repo.save_if_absent(snapshot)


def materialize_all_strategies(
    *, data_as_of: date | None = None,
    repository: SQLiteStrategyRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
) -> list[StrategyRunSnapshot]:
    repo = repository or SQLiteStrategyRepository()
    runs = [
        materialize_strategy_run(
            strategy_id,
            data_as_of=data_as_of,
            repository=repo,
            first_board_repository=first_board_repository,
            limit_up_repository=limit_up_repository,
        )
        for strategy_id in STRATEGY_IDS
    ]
    repo.backfill_outcomes()
    return runs


def strategy_stock_path(strategy_id: str, symbol: str, *, data_as_of: date) -> dict[str, Any]:
    shape = strategy_shape(strategy_id)
    contract = PostLimitQueryContract(
        mode="path", shape=shape or "high_drawdown", data_as_of=data_as_of,
        symbol=symbol, recent_limit_days=20,
    )
    return build_post_limit_path(load_post_limit_dataset(data_as_of), contract, symbol=symbol)


def strategy_statistics(strategy_id: str, *, data_as_of: date | None, days: int) -> dict[str, Any]:
    if strategy_id == "relay_one_to_two":
        repo = SQLiteStrategyRepository()
        runs = repo.list_runs(strategy_id, limit=days)
        return {
            "status": "ready" if runs else "empty",
            "strategy_id": strategy_id,
            "run_count": len(runs),
            "signal_dates": [run.signal_date.isoformat() for run in runs],
            "candidate_count": sum(run.candidate_count for run in runs),
            "sample_quality": "sufficient" if len(runs) >= 60 else "insufficient",
            "comparison_allowed": False,
            "warnings": ["少于60个成熟前向结果日时，不据此判断策略有效性。"] if len(runs) < 60 else [],
        }
    shape = strategy_shape(strategy_id)
    contract = PostLimitQueryContract(
        mode="statistics", shape=shape, shapes=(shape,), data_as_of=data_as_of,
        statistics_days=max(1, min(days, 30)), exhaustive=True, limit=100,
    )
    return build_post_limit_statistics(load_post_limit_dataset(data_as_of), contract)


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

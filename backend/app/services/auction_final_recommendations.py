"""Finalize both pre-market strategies with the 09:25 closing auction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Literal, Sequence
from zoneinfo import ZoneInfo

from app.agents.first_board import build_first_board_ratings
from app.collectors.hithink_finance_collector import (
    HithinkAuctionFact,
    HithinkAuctionSnapshot,
    HithinkFinanceCollector,
)
from app.models import (
    AuctionFinalCandidate,
    AuctionFinalRecommendationsResponse,
)
from app.repositories import (
    SQLiteAuctionFinalRepository,
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteScoringPolicyRepository,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AUCTION_FINAL_SCORING_VERSION = "auction-final-v1"
AUCTION_BASE_WEIGHT = 0.8
AUCTION_SCORE_MAX = 20.0
FINAL_TOP_K = 10
DISCOVERY_POOL_LIMIT = 30


class AuctionFinalizationError(RuntimeError):
    """Raised when a trustworthy current-day final snapshot cannot be built."""


AuctionCollector = Callable[[Sequence[str]], HithinkAuctionSnapshot]


@dataclass(frozen=True)
class _BaseCandidate:
    strategy: Literal["discovery", "relay"]
    base_trade_date: date
    symbol: str
    name: str
    sector: str
    position_label: str | None
    rank: int
    score: float
    scoring_version: str = ""


def finalize_auction_recommendations(
    *,
    trade_date: date | None = None,
    now: datetime | None = None,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    discovery_repository: SQLiteFirstBoardDiscoveryRepository | None = None,
    snapshot_repository: SQLiteAuctionFinalRepository | None = None,
    auction_collector: AuctionCollector | None = None,
) -> AuctionFinalRecommendationsResponse:
    """Build and persist an immutable final Top10 for each strategy."""

    finalized_at = _as_shanghai(now or datetime.now(SHANGHAI_TZ))
    target_date = trade_date or finalized_at.date()
    first_repo = first_board_repository or SQLiteFirstBoardRepository()
    limit_repo = limit_up_repository or SQLiteLimitUpRepository(seed_if_empty=False)
    discovery_repo = discovery_repository or SQLiteFirstBoardDiscoveryRepository(
        first_repo.database_path
    )
    final_repo = snapshot_repository or SQLiteAuctionFinalRepository(
        first_repo.database_path
    )
    existing = final_repo.get(target_date)
    if existing is not None:
        return existing

    base_candidates, discovery_date, relay_date, warnings = _load_base_candidates(
        target_date=target_date,
        limit_up_repository=limit_repo,
        first_board_repository=first_repo,
        discovery_repository=discovery_repo,
    )
    if not any(item.strategy == "discovery" for item in base_candidates):
        raise AuctionFinalizationError(
            "No first-board discovery pool matches the target trading day."
        )
    if not base_candidates:
        raise AuctionFinalizationError("No eligible pre-market candidate pool is available.")

    active_collector = auction_collector or (
        lambda thscodes: HithinkFinanceCollector().collect_auction_snapshots(
            thscodes,
            stage="final",
        )
    )
    auction = active_collector([_to_thscode(item.symbol) for item in base_candidates])
    captured_date = _as_shanghai(auction.captured_at).date()
    if captured_date != target_date:
        raise AuctionFinalizationError(
            "Auction provider did not return the requested current trading day."
        )
    if auction.data_status.lower() != "final" or auction.auction_phase.lower() != "closed":
        raise AuctionFinalizationError(
            "Closing auction is not final yet; retry after 09:25:10."
        )

    facts_by_symbol = {item.symbol: item for item in auction.items}
    ranked: list[AuctionFinalCandidate] = []
    for candidate in base_candidates:
        fact = facts_by_symbol.get(candidate.symbol)
        if fact is None or fact.auction_price is None or fact.auction_pct is None:
            warnings.append(f"{candidate.name}竞价终值缺失，未进入终选。")
            continue
        auction_score, reasons, risks = score_auction_fact(fact)
        ranked.append(
            AuctionFinalCandidate(
                strategy=candidate.strategy,
                base_trade_date=candidate.base_trade_date,
                target_trade_date=target_date,
                symbol=candidate.symbol,
                name=candidate.name,
                sector=candidate.sector,
                position_label=candidate.position_label,
                base_rank=candidate.rank,
                base_score=candidate.score,
                base_scoring_version=candidate.scoring_version,
                final_rank=0,
                final_score=round(
                    candidate.score * AUCTION_BASE_WEIGHT + auction_score,
                    1,
                ),
                auction_score=auction_score,
                auction_price=fact.auction_price,
                auction_pct=fact.auction_pct,
                auction_volume=fact.auction_volume,
                auction_amount=fact.auction_amount,
                auction_unmatched=fact.auction_unmatched,
                auction_turnover_pct=fact.auction_turnover_pct,
                auction_yesterday_ratio_pct=fact.auction_yesterday_ratio_pct,
                auction_volume_ratio=fact.auction_volume_ratio,
                previous_close=fact.previous_close,
                float_market_cap=fact.float_market_cap,
                reasons=reasons,
                risks=risks,
            )
        )

    final_candidates: list[AuctionFinalCandidate] = []
    for strategy in ("discovery", "relay"):
        strategy_items = sorted(
            [item for item in ranked if item.strategy == strategy],
            key=lambda item: (-item.final_score, item.base_rank, item.symbol),
        )[:FINAL_TOP_K]
        final_candidates.extend(
            item.model_copy(update={"final_rank": index})
            for index, item in enumerate(strategy_items, start=1)
        )
        expected = min(
            FINAL_TOP_K,
            sum(item.strategy == strategy for item in base_candidates),
        )
        if len(strategy_items) < expected:
            warnings.append(
                f"{_strategy_label(strategy)}竞价数据完整候选仅 {len(strategy_items)} 只。"
            )

    response = AuctionFinalRecommendationsResponse(
        trade_date=target_date,
        finalized_at=finalized_at,
        auction_captured_at=auction.captured_at,
        status="partial" if warnings else "complete",
        auction_phase=auction.auction_phase,
        data_status=auction.data_status,
        scoring_version=AUCTION_FINAL_SCORING_VERSION,
        source=auction.source,
        discovery_base_date=discovery_date,
        relay_base_date=relay_date,
        candidates=final_candidates,
        warnings=list(dict.fromkeys(warnings)),
    )
    final_repo.save(response)
    return final_repo.get(target_date) or response


def score_auction_fact(
    fact: HithinkAuctionFact,
) -> tuple[float, list[str], list[str]]:
    """Score auction confirmation on a bounded 20-point deterministic scale."""

    pct = fact.auction_pct
    if pct is None:
        return 0.0, [], ["竞价涨幅缺失"]
    reasons: list[str] = []
    risks: list[str] = []

    if 1 <= pct < 5:
        gap_score = 10.0
        reasons.append(f"竞价高开 {pct:.2f}%，处于温和确认区间")
    elif 5 <= pct < 8:
        gap_score = 7.0
        reasons.append(f"竞价高开 {pct:.2f}%，强度较高")
        risks.append("高开幅度偏大，开盘承接仍需观察")
    elif 0 <= pct < 1:
        gap_score = 5.0
        reasons.append(f"竞价微幅高开 {pct:.2f}%")
    elif -2 <= pct < 0:
        gap_score = 2.0
        risks.append(f"竞价低开 {abs(pct):.2f}%，确认度偏弱")
    elif 8 <= pct < 9.5:
        gap_score = 3.0
        risks.append(f"竞价高开 {pct:.2f}%，透支风险较高")
    elif pct >= 9.5:
        gap_score = 0.0
        risks.append("竞价接近一字涨停，可参与性和换手确认不足")
    else:
        gap_score = 0.0
        risks.append(f"竞价低开 {abs(pct):.2f}%，弱于预期")

    volume_ratio = fact.auction_volume_ratio
    if volume_ratio is None:
        volume_score = 0.0
        risks.append("竞价量比缺失")
    elif volume_ratio >= 1.5:
        volume_score = 5.0
        reasons.append(f"竞价量比 {volume_ratio:.2f}，量能确认充分")
    elif volume_ratio >= 1:
        volume_score = 4.0
        reasons.append(f"竞价量比 {volume_ratio:.2f}，量能高于基准")
    elif volume_ratio >= 0.6:
        volume_score = 3.0
        reasons.append(f"竞价量比 {volume_ratio:.2f}，量能尚可")
    elif volume_ratio >= 0.3:
        volume_score = 1.5
        risks.append(f"竞价量比 {volume_ratio:.2f}，量能偏弱")
    else:
        volume_score = 0.0
        risks.append(f"竞价量比 {volume_ratio:.2f}，量能不足")

    turnover = fact.auction_turnover_pct
    if turnover is None:
        turnover_score = 0.0
        risks.append("竞价换手率缺失")
    elif 0.03 <= turnover <= 0.3:
        turnover_score = 5.0
        reasons.append(f"竞价换手 {turnover:.3f}%，资金参与度较好")
    elif 0.01 <= turnover < 0.03:
        turnover_score = 3.0
        reasons.append(f"竞价换手 {turnover:.3f}%，资金参与度一般")
    elif turnover > 0.3:
        turnover_score = 3.0
        risks.append(f"竞价换手 {turnover:.3f}%，分歧偏大")
    else:
        turnover_score = 0.0
        risks.append(f"竞价换手 {turnover:.3f}%，资金参与度不足")

    return (
        round(min(AUCTION_SCORE_MAX, gap_score + volume_score + turnover_score), 1),
        reasons,
        risks,
    )


def _load_base_candidates(
    *,
    target_date: date,
    limit_up_repository: SQLiteLimitUpRepository,
    first_board_repository: SQLiteFirstBoardRepository,
    discovery_repository: SQLiteFirstBoardDiscoveryRepository,
) -> tuple[list[_BaseCandidate], date | None, date | None, list[str]]:
    warnings: list[str] = []
    candidates: list[_BaseCandidate] = []
    discovery = discovery_repository.get_latest()
    discovery_date = discovery.data_as_of if discovery else None
    if discovery is None or discovery.target_trade_date != target_date:
        warnings.append("首板挖掘没有匹配当日的盘前候选池。")
    else:
        if len(discovery.candidates) < DISCOVERY_POOL_LIMIT:
            warnings.append(
                f"首板挖掘历史快照仅有 {len(discovery.candidates)} 只，按现有名单重排。"
            )
        candidates.extend(
            _BaseCandidate(
                strategy="discovery",
                base_trade_date=discovery.data_as_of,
                symbol=item.facts.symbol,
                name=item.facts.name,
                sector=item.facts.themes[0].name if item.facts.themes else "",
                position_label=item.facts.pattern,
                rank=index,
                score=item.score,
                scoring_version=discovery.generated_by,
            )
            for index, item in enumerate(
                discovery.candidates[:DISCOVERY_POOL_LIMIT],
                start=1,
            )
        )

    events = [
        item
        for item in limit_up_repository.list_events()
        if item.trade_date < target_date
    ]
    relay_date = max((item.trade_date for item in events), default=None)
    if relay_date is None:
        warnings.append("一进二接力没有可用的上一交易日首板池。")
    else:
        live_snapshot = first_board_repository.get_live_prediction_snapshot(relay_date)
        scoring_policy = None
        if live_snapshot is not None:
            scoring_policy = SQLiteScoringPolicyRepository(
                first_board_repository.database_path
            ).get_policy(live_snapshot.generated_by)
        ratings = build_first_board_ratings(
            events=events,
            trade_date=relay_date,
            first_board_repository=first_board_repository,
            scoring_policy=scoring_policy,
        )
        candidates.extend(
            _BaseCandidate(
                strategy="relay",
                base_trade_date=relay_date,
                symbol=item.facts.symbol,
                name=item.facts.name,
                sector=item.facts.concept or item.facts.industry,
                position_label=(
                    item.facts.enrichment.position.primary.label
                    if item.facts.enrichment and item.facts.enrichment.position
                    else None
                ),
                rank=index,
                score=item.score,
                scoring_version=ratings.generated_by,
            )
            for index, item in enumerate(ratings.candidates, start=1)
        )
    return candidates, discovery_date, relay_date, warnings


def _to_thscode(symbol: str) -> str:
    return f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"


def _strategy_label(strategy: str) -> str:
    return "首板挖掘" if strategy == "discovery" else "一进二接力"


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)

from datetime import date, time

from app.collectors.hithink_finance_collector import (
    HithinkDragonTigerFact,
    HithinkDragonTigerSnapshot,
)
from app.models import LimitUpEvent
from app.services.dragon_tiger_review import build_dragon_tiger_review


def _fact(
    symbol: str,
    *,
    net_buy_amount: float,
    range_days: int,
    organization_net_buy_amount: float | None = None,
    hot_money_net_buy_amount: float | None = None,
) -> HithinkDragonTigerFact:
    return HithinkDragonTigerFact(
        symbol=symbol,
        thscode=f"{symbol}.SZ",
        name=f"股票{symbol}",
        change_pct=10.0,
        buy_amount=200.0,
        sell_amount=100.0,
        net_buy_amount=net_buy_amount,
        net_rate=5.0,
        organization_net_buy_amount=organization_net_buy_amount,
        hot_money_net_buy_amount=hot_money_net_buy_amount,
        hot_rank=8,
        range_days=range_days,
        limit_reason="日涨幅偏离值达7%",
        concepts=["算力", "算力"],
    )


def _event(symbol: str) -> LimitUpEvent:
    return LimitUpEvent(
        symbol=symbol,
        name=f"股票{symbol}",
        trade_date=date(2026, 8, 25),
        first_limit_time=time(9, 35),
        last_limit_time=time(14, 50),
        seal_count=2,
        break_count=1,
        closed_limit=True,
        board_height=1,
        amount=1_000_000_000,
        turnover_rate=12.0,
        industry="软件服务",
        concept="算力",
        next_open_pct=0,
        next_high_pct=0,
        next_close_pct=0,
        three_day_return_pct=0,
        five_day_return_pct=0,
        continued_next_day=False,
    )


def test_build_dragon_tiger_review_deduplicates_and_links_local_event() -> None:
    snapshot = HithinkDragonTigerSnapshot(
        trade_date=date(2026, 8, 25),
        board_type="all",
        stock_count=2,
        items=[
            _fact("000001", net_buy_amount=500.0, range_days=3),
            _fact(
                "000001",
                net_buy_amount=100.0,
                range_days=1,
                organization_net_buy_amount=20.0,
            ),
            _fact(
                "000002",
                net_buy_amount=-30.0,
                range_days=1,
                hot_money_net_buy_amount=-10.0,
            ),
            _fact("111026", net_buy_amount=900.0, range_days=1),
            _fact("688169", net_buy_amount=800.0, range_days=1),
            _fact("830001", net_buy_amount=700.0, range_days=1),
        ],
    )

    response = build_dragon_tiger_review(
        snapshot,
        [_event("000001")],
        trade_date=date(2026, 8, 25),
    )

    assert response.stock_count == 2
    assert response.net_inflow_count == 1
    assert response.net_outflow_count == 1
    assert response.organization_count == 1
    assert response.hot_money_count == 1
    assert response.items[0].symbol == "000001"
    assert response.items[0].net_buy_amount == 100.0
    assert response.items[0].concepts == ["算力"]
    assert response.items[0].detail_trade_date == date(2026, 8, 25)
    assert response.items[1].detail_trade_date is None
    assert {item.symbol for item in response.items}.isdisjoint(
        {"111026", "688169", "830001"}
    )

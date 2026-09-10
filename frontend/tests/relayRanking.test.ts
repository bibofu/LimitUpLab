import assert from "node:assert/strict";
import test from "node:test";

import {
  displayRelayPositionLabel,
  latestRelayCandidates,
  rankedRelayCandidates,
  sortFirstBoardByRelayRanking,
} from "../src/relayRanking.ts";

test("relay cards expose the first-board position with an explicit missing fallback", /* Regression scenario: relay cards expose the first-board position with an explicit missing fallback. */ () => {
  assert.equal(displayRelayPositionLabel("低位启动首板"), "低位启动首板");
  assert.equal(displayRelayPositionLabel("  高位震荡首板  "), "高位震荡首板");
  assert.equal(displayRelayPositionLabel(null), "首板位置待补充");
});

test("the first-board table uses the same dynamic Top10 order as pre-market", /* Regression scenario: the first-board table uses the same dynamic Top10 order as pre-market. */ () => {
  const tradeDate = "2026-09-02";
  const events = Array.from({ length: 12 }, /* Handle the callback from Array.from within this view. */ (_, index) => ({
    symbol: String(index + 1).padStart(6, "0"),
    first_limit_time: `09:${String(index + 25).padStart(2, "0")}:00`,
  }));
  const dynamicSymbols = ["000012", "000009", "000002", "000007", "000004",
    "000010", "000001", "000006", "000003", "000011"];
  const intelligence = dynamicSymbols.map(/* Transform each entry in dynamicSymbols into the result used by this view. */ (symbol, index) => ({
    strategy: "relay" as const,
    base_trade_date: tradeDate,
    symbol,
    rank: index + 1,
    draft_score: 90 - index,
  }));
  const ratings = new Map(events.map(/* Transform each entry in events into the result used by this view. */ (event, index) => [event.symbol, 100 - index]));

  const recommendationTop10 = rankedRelayCandidates(intelligence, tradeDate, 10);
  const poolOrder = sortFirstBoardByRelayRanking(events, recommendationTop10, ratings);

  assert.deepEqual(
    poolOrder.slice(0, 10).map(/* Transform each entry in poolOrder.slice(0, 10) into the result used by this view. */ (item) => item.symbol),
    recommendationTop10.map(/* Transform each entry in recommendationTop10 into the result used by this view. */ (item) => item.symbol),
  );
  assert.equal(poolOrder[0].symbol, "000012", "a low base-score stock can move into Top10");
});

test("a stale dynamic snapshot cannot reorder another trade date", /* Regression scenario: a stale dynamic snapshot cannot reorder another trade date. */ () => {
  const events = [
    { symbol: "000001", first_limit_time: "09:31:00" },
    { symbol: "000002", first_limit_time: "09:30:00" },
  ];
  const stale = [{
    strategy: "relay" as const,
    base_trade_date: "2026-09-01",
    symbol: "000001",
    rank: 1,
    draft_score: 99,
  }];
  const ratings = new Map([["000002", 80], ["000001", 70]]);

  const currentRanking = rankedRelayCandidates(stale, "2026-09-02");
  const poolOrder = sortFirstBoardByRelayRanking(events, currentRanking, ratings);

  assert.deepEqual(currentRanking, []);
  assert.deepEqual(poolOrder.map(/* Transform each entry in poolOrder into the result used by this view. */ (item) => item.symbol), ["000002", "000001"]);
});

test("the pre-market page defaults to the latest available relay snapshot", /* Regression scenario: the pre-market page defaults to the latest available relay snapshot. */ () => {
  const snapshots = [
    {
      strategy: "relay" as const,
      base_trade_date: "2026-09-05",
      symbol: "000001",
      rank: 1,
      draft_score: 90,
    },
    {
      strategy: "relay" as const,
      base_trade_date: "2026-09-07",
      symbol: "000002",
      rank: 2,
      draft_score: 88,
    },
    {
      strategy: "relay" as const,
      base_trade_date: "2026-09-07",
      symbol: "000003",
      rank: 1,
      draft_score: 89,
    },
  ];

  assert.deepEqual(
    latestRelayCandidates(snapshots).map(/* Transform each entry in latestRelayCandidates(snapshots) into the result used by this view. */ (item) => item.symbol),
    ["000003", "000002"],
  );
});

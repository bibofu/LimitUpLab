import assert from "node:assert/strict";
import test from "node:test";

import {
  rankedRelayCandidates,
  sortFirstBoardByRelayRanking,
} from "../src/relayRanking.ts";

test("the first-board table uses the same dynamic Top10 order as pre-market", () => {
  const tradeDate = "2026-09-02";
  const events = Array.from({ length: 12 }, (_, index) => ({
    symbol: String(index + 1).padStart(6, "0"),
    first_limit_time: `09:${String(index + 25).padStart(2, "0")}:00`,
  }));
  const dynamicSymbols = ["000012", "000009", "000002", "000007", "000004",
    "000010", "000001", "000006", "000003", "000011"];
  const intelligence = dynamicSymbols.map((symbol, index) => ({
    strategy: "relay" as const,
    base_trade_date: tradeDate,
    symbol,
    rank: index + 1,
    draft_score: 90 - index,
  }));
  const ratings = new Map(events.map((event, index) => [event.symbol, 100 - index]));

  const recommendationTop10 = rankedRelayCandidates(intelligence, tradeDate, 10);
  const poolOrder = sortFirstBoardByRelayRanking(events, recommendationTop10, ratings);

  assert.deepEqual(
    poolOrder.slice(0, 10).map((item) => item.symbol),
    recommendationTop10.map((item) => item.symbol),
  );
  assert.equal(poolOrder[0].symbol, "000012", "a low base-score stock can move into Top10");
});

test("a stale dynamic snapshot cannot reorder another trade date", () => {
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
  assert.deepEqual(poolOrder.map((item) => item.symbol), ["000002", "000001"]);
});

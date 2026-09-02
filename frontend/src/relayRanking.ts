export interface RelayRankingItem {
  strategy: "discovery" | "relay";
  base_trade_date: string;
  symbol: string;
  rank: number;
  draft_score: number;
}

export interface FirstBoardSortableEvent {
  symbol: string;
  first_limit_time: string;
}

export function isRelayCandidateSymbol(symbol: string) {
  return !symbol.startsWith("300") && !symbol.startsWith("301");
}

export function rankedRelayCandidates<T extends RelayRankingItem>(
  items: readonly T[],
  tradeDate: string | undefined,
  limit?: number,
) {
  if (!tradeDate) return [];
  const ranked = items
    .filter(
      (item) =>
        item.strategy === "relay"
        && item.base_trade_date === tradeDate
        && isRelayCandidateSymbol(item.symbol),
    )
    .sort((left, right) => left.rank - right.rank || left.symbol.localeCompare(right.symbol));
  return limit === undefined ? ranked : ranked.slice(0, Math.max(0, limit));
}

export function sortFirstBoardByRelayRanking<T extends FirstBoardSortableEvent>(
  events: readonly T[],
  relayRanking: readonly RelayRankingItem[],
  ratingScores: ReadonlyMap<string, number>,
) {
  const dynamicRank = new Map(relayRanking.map((item) => [item.symbol, item.rank]));
  return [...events].sort((left, right) => {
    const leftRank = dynamicRank.get(left.symbol);
    const rightRank = dynamicRank.get(right.symbol);
    if (leftRank !== undefined && rightRank !== undefined) return leftRank - rightRank;
    if (leftRank !== undefined) return -1;
    if (rightRank !== undefined) return 1;

    const leftScore = ratingScores.get(left.symbol);
    const rightScore = ratingScores.get(right.symbol);
    if (leftScore !== undefined && rightScore !== undefined) return rightScore - leftScore;
    if (leftScore !== undefined) return -1;
    if (rightScore !== undefined) return 1;
    return left.first_limit_time.localeCompare(right.first_limit_time);
  });
}

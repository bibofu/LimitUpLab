export interface RelayRankingItem {
  strategy: "relay";
  base_trade_date: string;
  symbol: string;
  rank: number;
  draft_score: number;
}

export interface FirstBoardSortableEvent {
  symbol: string;
  first_limit_time: string;
}

/**
 * Exclude the ChiNext code prefixes from relay display. Full research eligibility is
 * determined by the backend; this helper only applies that prefix exclusion.
 */
export function isRelayCandidateSymbol(symbol: string) {
  return !symbol.startsWith("300") && !symbol.startsWith("301");
}

/**
 * Render the position label associated with a relay candidate's evidence.
 */
export function displayRelayPositionLabel(positionLabel: string | null | undefined) {
  const normalized = positionLabel?.trim();
  return normalized || "首板位置待补充";
}

/**
 * Filter the requested relay cohort and order it by the supplied rank, breaking ties by
 * symbol. This displays an existing ranking rather than computing new scores.
 */
export function rankedRelayCandidates<T extends RelayRankingItem>(
  items: readonly T[],
  tradeDate: string | undefined,
  limit?: number,
) {
  if (!tradeDate) return [];
  const ranked = items
    .filter(
      /* Keep only entries satisfying this predicate for rankedRelayCandidates. */ (item) =>
        item.strategy === "relay"
        && item.base_trade_date === tradeDate
        && isRelayCandidateSymbol(item.symbol),
    )
    .sort(/* Compare two entries using the explicit tie-break order for rankedRelayCandidates. */ (left, right) => left.rank - right.rank || left.symbol.localeCompare(right.symbol));
  return limit === undefined ? ranked : ranked.slice(0, Math.max(0, limit));
}

/**
 * Select candidates belonging to the latest applicable relay snapshot.
 */
export function latestRelayCandidates<T extends RelayRankingItem>(
  items: readonly T[],
  limit?: number,
) {
  const latestTradeDate = items.reduce<string | undefined>(/* Accumulate the entries into the derived value used by latestRelayCandidates. */ (latest, item) => {
    if (item.strategy !== "relay" || !isRelayCandidateSymbol(item.symbol)) return latest;
    return latest === undefined || item.base_trade_date > latest
      ? item.base_trade_date
      : latest;
  }, undefined);
  return rankedRelayCandidates(items, latestTradeDate, limit);
}

/**
 * Order first-board rows by the relay ranking while retaining deterministic order for
 * unmatched rows.
 */
export function sortFirstBoardByRelayRanking<T extends FirstBoardSortableEvent>(
  events: readonly T[],
  relayRanking: readonly RelayRankingItem[],
  ratingScores: ReadonlyMap<string, number>,
) {
  const dynamicRank = new Map(relayRanking.map(/* Transform each entry in relayRanking into the result used by sortFirstBoardByRelayRanking. */ (item) => [item.symbol, item.rank]));
  return [...events].sort(/* Compare two entries using the explicit tie-break order for sortFirstBoardByRelayRanking. */ (left, right) => {
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

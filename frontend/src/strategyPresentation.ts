import type { StrategyDefinition, StrategyOutputType } from "./types";

export const STRATEGY_DISPLAY_ORDER = [
  "relay_one_to_two",
  "high_drawdown",
  "volume_consolidation",
  "pullback_stabilizing",
  "strong_nonconsecutive",
  "broken_board_repair",
  "second_to_third",
] as const;

export function orderStrategies<T extends Pick<StrategyDefinition, "strategy_id">>(strategies: T[]): T[] {
  const positions = new Map<string, number>(
    STRATEGY_DISPLAY_ORDER.map((strategyId, index) => [strategyId, index]),
  );
  return [...strategies].sort(
    (left, right) => (positions.get(left.strategy_id) ?? 999) - (positions.get(right.strategy_id) ?? 999),
  );
}

export function canShowStrategyRanking(outputType: StrategyOutputType): boolean {
  return outputType === "ranked_research";
}

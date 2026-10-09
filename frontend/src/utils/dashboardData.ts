import type { FirstBoardRatingsResponse, LimitUpEvent } from "../types";

export function latestEventDate(events: LimitUpEvent[] | null): string | undefined {
  return events?.map(event => event.trade_date).sort().pop();
}

/** Ratings may arrive before or after events; only join verified identical dates. */
export function matchingRatings(events: LimitUpEvent[], ratings: FirstBoardRatingsResponse | null | undefined) {
  return ratings && events.length > 0 && events.every(event => event.trade_date === ratings.trade_date)
    ? ratings : undefined;
}

import { financeNewsSnapshot } from "../utils/marketSnapshots";
import { usePublicSnapshot } from "./usePublicSnapshot";

export function useFinanceNews(page: number) {
  const resource = useMemo(() => financeNewsSnapshot(page), [page]);
  const state = usePublicSnapshot(resource, true, 5 * 60_000);
  return { ...state, news: state.data };
}
import { useMemo } from "react";

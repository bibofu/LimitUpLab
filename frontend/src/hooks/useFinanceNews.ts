import { useEffect, useMemo, useState } from "react";
import { financeNewsSnapshot, latestFinanceNewsSnapshot } from "../utils/marketSnapshots";
import { followNewsRefresh } from "../utils/newsRefreshPolling";
import { usePublicSnapshot } from "./usePublicSnapshot";

export function useFinanceNews(page: number, mode: "latest" | "history" = "latest") {
  const resource = useMemo(() => mode === "latest" ? latestFinanceNewsSnapshot : financeNewsSnapshot(page), [page, mode]);
  const state = usePublicSnapshot(resource, true, 5 * 60_000);
  const [retry, setRetry] = useState(0);
  const [waitingTooLong, setWaitingTooLong] = useState(false);
  const shouldFollow = mode === "latest" && !!state.data?.refreshing && !state.error && !state.data?.refresh_error;
  useEffect(() => {
    setWaitingTooLong(false);
    if (!shouldFollow) return;
    return followNewsRefresh(resource, () => setWaitingTooLong(true));
  }, [resource, shouldFollow, retry]);
  return { ...state, news: state.data, waitingTooLong,
    reload: () => { setRetry(value => value + 1); return state.reload(); } };
}

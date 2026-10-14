import type { FinanceNewsPage } from "../types";
import { createPublicSnapshot } from "./publicSnapshot";

export const LATEST_NEWS_MAX_AGE_MS = 7 * 24 * 60 * 60_000;
type NewsStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** Keep a dated last-known batch; old archive caches are explicitly unverified. */
export function createLatestNewsSnapshot({ key, legacyKey, fetchValue, valid,
  storage = () => typeof window === "undefined" ? null : window.localStorage,
  now = Date.now,
}: {
  key: string;
  legacyKey: string;
  fetchValue: () => Promise<FinanceNewsPage>;
  valid: (value: unknown) => value is FinanceNewsPage;
  storage?: () => NewsStorage | null;
  now?: () => number;
}) {
  const withinSourceAge = (value: FinanceNewsPage) => {
    const fetchedAt = Date.parse(value.fetched_at);
    return Number.isFinite(fetchedAt) && fetchedAt <= now() && now() - fetchedAt <= LATEST_NEWS_MAX_AGE_MS;
  };
  const validCurrent = (value: unknown): value is FinanceNewsPage => valid(value) && withinSourceAge(value);
  const cacheStorage = (): NewsStorage | null => {
    const disk = storage();
    if (!disk) return null;
    return {
      getItem(storageKey) {
        const current = disk.getItem(storageKey);
        const raw = current ?? disk.getItem(`limituplab.public-snapshot.v1:${legacyKey}`);
        if (!raw || raw.length > 512_000) return null;
        try {
          const saved = JSON.parse(raw);
          if (saved?.version !== 1 || saved.data?.page !== 1 || !Array.isArray(saved.data?.items)) return null;
          const data: FinanceNewsPage = { ...saved.data, page: 1, total_pages: 1,
            total: saved.data.items.length, stale: true, refreshing: false, refresh_error: null };
          if (!validCurrent(data)) return null;
          // Writing an old response again must not extend the source's retention limit.
          return JSON.stringify({ ...saved, data });
        } catch { return null; }
      },
      setItem: (storageKey, value) => disk.setItem(storageKey, value),
      removeItem: storageKey => disk.removeItem(storageKey),
    };
  };
  return createPublicSnapshot({ key, fetchValue, valid: validCurrent, storage: cacheStorage, now, retain: withinSourceAge,
    maxAgeMs: LATEST_NEWS_MAX_AGE_MS });
}

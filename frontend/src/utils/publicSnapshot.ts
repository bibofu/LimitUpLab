/** Display-only public data. Never use this cache for private sessions or Agent evidence. */
export interface PublicSnapshotState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  fromCache: boolean;
  savedAt: number | null;
}

type SnapshotStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function createPublicSnapshot<T>({
  key, fetchValue, valid, maxAgeMs, freshForMs = 30_000,
  storage = () => typeof window === "undefined" ? null : window.localStorage,
  now = Date.now,
  retain = () => true,
}: {
  key: string;
  fetchValue: () => Promise<T>;
  valid: (value: unknown) => value is T;
  maxAgeMs: number;
  freshForMs?: number;
  storage?: () => SnapshotStorage | null;
  now?: () => number;
  retain?: (value: T) => boolean;
}) {
  const storageKey = `limituplab.public-snapshot.v1:${key}`;
  const listeners = new Set<() => void>();
  const empty = (): PublicSnapshotState<T> => ({
    data: null, loading: false, error: null, fromCache: false, savedAt: null,
  });
  let state = empty();
  let initialized = false;
  let pending: Promise<void> | null = null;
  const unexpired = (time: number) => Number.isFinite(time) && time <= now() && now() - time <= maxAgeMs;

  function read() {
    if (!initialized) {
      initialized = true;
      try {
        const raw = storage()?.getItem(storageKey);
        if (raw && raw.length <= 512_000) {
          const saved = JSON.parse(raw);
          if (saved?.version === 1 && typeof saved.savedAt === "number"
              && unexpired(saved.savedAt) && valid(saved.data)) {
            state = { data: saved.data, loading: false, error: null, fromCache: true, savedAt: saved.savedAt };
          } else storage()?.removeItem(storageKey);
        }
      } catch { /* Storage and invalid old snapshots must never block a network read. */ }
    }
    if (state.savedAt !== null && (!unexpired(state.savedAt) || (state.data !== null && !retain(state.data)))) {
      state = { ...empty(), loading: state.loading, error: state.error };
    }
    return state;
  }

  function publish(value: PublicSnapshotState<T>) {
    state = value;
    listeners.forEach(listener => listener());
  }

  function refresh(force = false): Promise<void> {
    if (pending) return pending;
    const previous = read();
    if (!force && previous.data !== null && !previous.fromCache && !previous.error
        && previous.savedAt !== null && now() - previous.savedAt < freshForMs) return Promise.resolve();
    publish({ ...previous, loading: true, error: null, fromCache: previous.data !== null });
    // Retain one request across StrictMode/remounts; readers don't cancel shared work.
    pending = Promise.resolve().then(fetchValue).then(data => {
      if (!valid(data)) throw new Error("返回数据格式不完整，请重试");
      const savedAt = now();
      try {
        const raw = JSON.stringify({ version: 1, savedAt, data });
        if (raw.length <= 512_000) storage()?.setItem(storageKey, raw);
      } catch { /* Memory and the visible response remain usable if storage is full. */ }
      publish({ data, loading: false, error: null, fromCache: false, savedAt });
    }).catch(error => {
      const available = read();
      publish({ ...available, loading: false, fromCache: available.data !== null,
        error: error instanceof Error ? error.message : "更新失败，请重试" });
    }).finally(() => { pending = null; });
    return pending;
  }

  return { read, refresh, isObserved: () => listeners.size > 0, subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  } };
}

export type PublicSnapshot<T> = ReturnType<typeof createPublicSnapshot<T>>;

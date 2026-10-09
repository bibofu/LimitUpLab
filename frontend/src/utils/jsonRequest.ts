export const GET_TIMEOUT_MS = 120_000;
export const DASHBOARD_TIMEOUT_MS = 20_000;

/** Bound reads, including the response body, without changing mutation or SSE budgets. */
export async function requestJson<T>(
  url: string,
  init: RequestInit = {},
  fetcher: typeof fetch = fetch,
  timeoutMs = GET_TIMEOUT_MS,
): Promise<T> {
  const isRead = (init.method ?? "GET").toUpperCase() === "GET";
  const controller = new AbortController();
  const signal = controller.signal;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectAbort: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const abort = (reason: unknown) => {
    controller.abort(reason);
    rejectAbort(reason);
  };
  const onAbort = () => abort(init.signal?.reason ?? new DOMException("请求已取消", "AbortError"));
  init.signal?.addEventListener("abort", onAbort, { once: true });
  if (init.signal?.aborted) onAbort();
  if (isRead) timer = setTimeout(() => abort(new Error("数据请求超时，请重试")), timeoutMs);
  try {
    const operation = async () => {
      if (signal.aborted) throw signal.reason;
      const response = await fetcher(url, { credentials: "include", ...init, signal });
      if (!response.ok) {
        let message = `请求失败（${response.status} ${response.statusText}）`;
        try {
          const payload = await response.json() as { detail?: unknown };
          if (typeof payload.detail === "string" && payload.detail.trim()) message = payload.detail.trim();
        } catch { /* Gateway HTML must not appear in the application. */ }
        throw new Error(message);
      }
      return await response.json() as T;
    };
    return await Promise.race([operation(), interrupted]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    init.signal?.removeEventListener("abort", onAbort);
  }
}

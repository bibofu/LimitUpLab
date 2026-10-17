import { requestJson } from "./utils/jsonRequest";
import { createQualityClient } from "./utils/agentQuality";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
/** Bound mutations as well as reads, so a lost response leaves a retryable UI. */
export async function qualityRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  init.signal?.addEventListener("abort", cancel, { once: true });
  if (init.signal?.aborted) cancel();
  const timer = setTimeout(cancel, 20_000);
  try { return await requestJson<T>(`${baseUrl}${path}`, { ...init, signal: controller.signal }); }
  finally { clearTimeout(timer); init.signal?.removeEventListener("abort", cancel); }
}
export const qualityApi = createQualityClient(qualityRequest);

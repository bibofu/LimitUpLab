import { useEffect, useRef, useState } from "react";
import { qualityApi } from "../qualityApi";
import { answerHash, type RunQuality } from "../utils/agentQuality";

/** One quality read per session refresh, never one request per historical message. */
export function useAnswerQuality(sessionId: string | null, revision: number) {
  const [state, setState] = useState<{ session: string | null; runs: Record<string, RunQuality>; ready: boolean; error: boolean }>({
    session: null, runs: {}, ready: false, error: false,
  });
  const [retry, setRetry] = useState(0);
  const requests = useRef(0);
  useEffect(() => {
    const sequence = ++requests.current;
    if (!sessionId) return;
    const controller = new AbortController();
    setState(current => ({ session: sessionId, runs: current.session === sessionId ? current.runs : {}, ready: false, error: false }));
    qualityApi.session(sessionId, controller.signal).then(result => {
      if (!controller.signal.aborted && sequence === requests.current) setState({ session: sessionId, runs: result.runs, ready: true, error: false });
    }).catch(() => {
      if (!controller.signal.aborted && sequence === requests.current) setState(current => ({ ...current, ready: false, error: true }));
    });
    return () => controller.abort();
  }, [sessionId, revision, retry]);
  return { runs: state.session === sessionId ? state.runs : {}, ready: state.session === sessionId && state.ready,
    error: state.session === sessionId && state.error, retry: () => setRetry(value => value + 1) };
}

/** A receipt confirms a rendered answer revision; it never means the user read it. */
export function useAnswerDelivery() {
  const delivered = useRef(new Set<string>());
  return async (runId: string, content: string) => {
    const hash = await answerHash(content);
    if (!hash) return;
    const key = `${runId}:${hash}`;
    if (delivered.current.has(key)) return;
    delivered.current.add(key);
    try { await qualityApi.delivery(runId, "displayed", hash); }
    catch { delivered.current.delete(key); /* Unknown delivery is retained; retry on next render/open. */ }
  };
}

import type { ChatSessionDetail, ChatSessionsResponse } from "../types";
import { HttpResponseError } from "./jsonRequest";

type Bootstrap = ChatSessionsResponse & { selected_session: ChatSessionDetail | null };
type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

/** Keep verified session recovery usable while frontend/backend versions roll out separately. */
export async function loadChatBootstrap(request: Request, sessionId?: string | null): Promise<Bootstrap> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const options = { cache: "no-store" } as const;
  try {
    return await request<Bootstrap>(`/api/agents/chat/bootstrap${query}`, options);
  } catch (error) {
    // Only an unavailable endpoint permits compatibility reads; auth, network,
    // and server failures must remain visible rather than masquerading as empty history.
    if (!(error instanceof HttpResponseError) || ![404, 405].includes(error.status)) throw error;
  }

  const list = () => request<ChatSessionsResponse>("/api/agents/chat/sessions?limit=30", options);
  const detail = (id: string) => request<ChatSessionDetail>(
    `/api/agents/chat/sessions/${encodeURIComponent(id)}`, options,
  );
  // The list establishes/verifies the HttpOnly owner cookie before any detail read.
  let response = await list();
  const candidates = [...new Set([sessionId, response.sessions[0]?.session_id].filter(
    (id): id is string => Boolean(id),
  ))];
  for (const id of candidates) {
    try {
      return { ...response, selected_session: await detail(id) };
    } catch (error) {
      // Deleted IDs and IDs from a previous visitor are both rejected by the server.
      if (!(error instanceof HttpResponseError) || error.status !== 404) throw error;
    }
  }
  if (response.sessions.length > 0) {
    // A listed session disappeared during recovery. Reconcile once, without an
    // unbounded retry loop or implicitly declaring unavailable history empty.
    response = await list();
    if (response.sessions[0]) {
      return { ...response, selected_session: await detail(response.sessions[0].session_id) };
    }
  }
  return { ...response, selected_session: null };
}

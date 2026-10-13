/** Lock synchronously, before React has rendered disabled controls. */
export function createChatOperationLock() {
  let owner: symbol | null = null;
  return {
    acquire(): (() => void) | null {
      if (owner !== null) return null;
      const token = Symbol("chat-operation");
      owner = token;
      return () => {
        if (owner === token) owner = null;
      };
    },
  };
}

/** Enter confirms IME candidates; Shift+Enter keeps a newline in the draft. */
export function shouldSubmitChatKey(event: {
  key: string;
  shiftKey: boolean;
  isComposing: boolean;
  keyCode: number;
}, composing = false): boolean {
  return event.key === "Enter" && !event.shiftKey
    && !event.isComposing && !composing && event.keyCode !== 229;
}

/** Explicit prompt buttons must not discard the user's unsent question. */
export function draftAfterChatSubmit(draft: string, explicitPrompt?: string): string {
  return explicitPrompt === undefined ? "" : draft;
}

/** Browser privacy settings may disable storage; server-backed chat must still work. */
export function readActiveChatSession(read: () => string | null): string | null {
  try { return read(); } catch { return null; }
}

export function rememberActiveChatSession(write: () => void): void {
  try { write(); } catch { /* Persistence is optional; the current session remains usable. */ }
}

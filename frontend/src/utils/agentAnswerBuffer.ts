/** Coalesce validated SSE chunks into one React update per animation frame. */
export function createAgentAnswerBuffer(
  publish: (content: string) => void,
  schedule: (callback: () => void) => number = requestAnimationFrame,
  cancel: (handle: number) => void = cancelAnimationFrame,
) {
  let characters: string[] = [];
  let frame: number | null = null;
  let disposed = false;
  let dirty = false;
  let visibleLength = 0;

  function clearFrame() {
    if (frame !== null) cancel(frame);
    frame = null;
  }

  function flush() {
    clearFrame();
    if (disposed || !dirty) return;
    dirty = false;
    if (characters.length < visibleLength) return;
    visibleLength = characters.length;
    publish(characters.join(""));
  }

  return {
    reset(visibleContent = "") {
      clearFrame();
      characters = [];
      dirty = false;
      visibleLength = Math.max(visibleLength, Array.from(visibleContent).length);
      // Reconnect replay must not blank the answer already on screen.
    },
    append(offset: number, delta: string) {
      if (disposed) return;
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > characters.length) {
        throw new Error("回答片段不连续，正在尝试恢复连接");
      }
      characters.length = offset;
      for (const character of delta) characters.push(character);
      dirty = true;
      if (frame === null) frame = schedule(flush);
    },
    flush,
    dispose() {
      clearFrame();
      disposed = true;
      characters = [];
    },
  };
}

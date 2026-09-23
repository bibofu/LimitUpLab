/** Coalesce live Unicode deltas; preserve a revision across read-only reconnects. */
export function createAgentAnswerBuffer(
  publish: (content: string) => void,
  schedule: (callback: () => void) => number = requestAnimationFrame,
  cancel: (handle: number) => void = cancelAnimationFrame,
) {
  let characters: string[] = [];
  let frame: number | null = null;
  let disposed = false;
  let dirty = false;
  let revision: number | null = null;
  let withdrawn = false;

  function clearFrame() {
    if (frame !== null) cancel(frame);
    frame = null;
  }

  function flush() {
    clearFrame();
    if (disposed || !dirty) return;
    dirty = false;
    publish(characters.join(""));
  }

  function clear() {
    const hadContent = characters.length > 0;
    clearFrame();
    characters = [];
    dirty = false;
    if (hadContent) publish("");
  }

  return {
    start(nextRevision: number) {
      if (disposed || nextRevision === revision) return;
      if (revision !== null && nextRevision < revision) return;
      revision = nextRevision;
      withdrawn = false;
      clear();
    },
    reset() {
      if (disposed) return;
      withdrawn = true;
      clear();
    },
    append(offset: number, delta: string) {
      if (disposed || withdrawn) return;
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > characters.length) {
        throw new Error("回答片段不连续，正在尝试恢复连接");
      }
      const incoming = Array.from(delta);
      const overlap = Math.min(characters.length - offset, incoming.length);
      for (let index = 0; index < overlap; index++) {
        if (characters[offset + index] !== incoming[index]) throw new Error("回答片段冲突，正在尝试恢复连接");
      }
      if (overlap === incoming.length) return;
      for (let index = overlap; index < incoming.length; index++) characters.push(incoming[index]);
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

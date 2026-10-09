export interface ResourceState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export const emptyResource = <T>(): ResourceState<T> => ({ data: null, loading: true, error: null });

/** Each resource publishes independently. Superseded and unmounted reads cannot publish. */
export function createResourceLoader<T>(
  fetchValue: (signal: AbortSignal) => Promise<T>,
  publish: (state: ResourceState<T>) => void,
) {
  let generation = 0;
  let controller: AbortController | undefined;
  const cancel = () => {
    generation++;
    controller?.abort();
  };
  const reload = async () => {
    cancel();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    publish(emptyResource());
    try {
      const data = await fetchValue(signal);
      if (current === generation) publish({ data, loading: false, error: null });
    } catch (error) {
      if (current === generation) publish({
        data: null, loading: false,
        error: error instanceof Error ? error.message : "数据暂不可用，请重试",
      });
    }
  };
  return { reload, cancel };
}

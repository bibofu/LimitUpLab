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
  keepPreviousData = false,
  initiallyEnabled = true,
) {
  let enabled = initiallyEnabled;
  let generation = 0;
  let previousData: T | null = null;
  let controller: AbortController | undefined;
  const cancel = () => {
    generation++;
    controller?.abort();
  };
  const reload = async () => {
    if (!enabled) return;
    cancel();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    publish({ data: previousData, loading: true, error: null });
    try {
      const data = await fetchValue(signal);
      if (current === generation) {
        if (keepPreviousData) previousData = data;
        publish({ data, loading: false, error: null });
      }
    } catch (error) {
      if (current === generation) publish({
        data: previousData, loading: false,
        error: error instanceof Error ? error.message : "数据暂不可用，请重试",
      });
    }
  };
  const setEnabled = (next: boolean) => {
    enabled = next;
    if (!enabled) cancel();
  };
  return { reload, cancel, setEnabled };
}

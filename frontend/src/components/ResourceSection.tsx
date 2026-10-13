import { useEffect, useMemo, useState, type ReactNode } from "react";
import { LoaderCircle, RefreshCcw } from "lucide-react";
import { createResourceLoader, emptyResource, type ResourceState } from "../utils/asyncResource";

export function useResource<T>(fetchValue: (signal: AbortSignal) => Promise<T>, keepPreviousData = false, enabled = true) {
  const [state, setState] = useState<ResourceState<T>>(emptyResource);
  const loader = useMemo(() => createResourceLoader(fetchValue, setState, keepPreviousData), [fetchValue, keepPreviousData]);
  useEffect(() => {
    loader.setEnabled(enabled);
    if (enabled) void loader.reload();
    return loader.cancel;
  }, [loader, enabled]);
  return enabled ? { ...state, reload: loader.reload }
    : { data: null, loading: false, error: null, reload: loader.reload };
}

export function ResourceNotice({ label, loading, error, retry }: {
  label: string; loading: boolean; error: string | null; retry: () => void;
}) {
  return (
    <div className="resource-notice" role="status">
      {loading ? <LoaderCircle aria-hidden="true" size={16} /> : null}
      <span><strong>{label}</strong>：{loading ? "正在加载" : error ?? "数据暂不可用"}</span>
      <button className="icon-button" type="button" onClick={retry} aria-label={`重新加载${label}`}>
        <RefreshCcw size={15} />
      </button>
    </div>
  );
}

export function ResourceSection<T>({ label, resource, children }: {
  label: string; resource: ResourceState<T> & { reload: () => void }; children: (data: T) => ReactNode;
}) {
  return resource.data !== null ? <>
    <div className="resource-refresh-status" role="status">
      {resource.loading || resource.error ? <>
        {resource.loading ? <LoaderCircle aria-hidden="true" size={14} /> : null}
        <span title={resource.error ?? undefined}>
          {label}：{resource.loading ? "正在更新，显示上次结果" : "更新失败，保留上次结果"}
        </span>
        {resource.error && !resource.loading ? (
          <button className="icon-button compact" type="button" onClick={resource.reload} aria-label={`重新加载${label}`}>
            <RefreshCcw size={14} />
          </button>
        ) : null}
      </> : null}
    </div>
    {children(resource.data)}
  </> : (
    <ResourceNotice label={label} loading={resource.loading} error={resource.error} retry={resource.reload} />
  );
}

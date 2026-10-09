import { useEffect, useMemo, useState, type ReactNode } from "react";
import { LoaderCircle, RefreshCcw } from "lucide-react";
import { createResourceLoader, emptyResource, type ResourceState } from "../utils/asyncResource";

export function useResource<T>(fetchValue: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<ResourceState<T>>(emptyResource);
  const loader = useMemo(() => createResourceLoader(fetchValue, setState), [fetchValue]);
  useEffect(() => {
    void loader.reload();
    return loader.cancel;
  }, [loader]);
  return { ...state, reload: loader.reload };
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
  return resource.data !== null ? children(resource.data) : (
    <ResourceNotice label={label} loading={resource.loading} error={resource.error} retry={resource.reload} />
  );
}

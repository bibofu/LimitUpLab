import { LoaderCircle, RefreshCcw, Sparkles } from "lucide-react";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import ReactMarkdown from "react-markdown";
import { streamReviewOverviewSummary } from "../api";
import { useReviewReport } from "../hooks/useReviewReport";
import { createReviewOverviewResource } from "../utils/reviewOverviewResource";

export function ReviewOverview({ endDate }: { endDate: string }) {
  const { report, loading } = useReviewReport(endDate);
  const resource = useMemo(() => createReviewOverviewResource(streamReviewOverviewSummary), []);
  const state = useSyncExternalStore(resource.subscribe, resource.getState, resource.getState);
  useEffect(() => () => resource.reset(), [resource, endDate]);
  useEffect(() => { if (loading) resource.reset(); }, [loading, resource]);
  const pending = state.status === "generating" || state.status === "checking";
  const label = state.status === "checking" ? "正在核验…" : pending ? "生成中…"
    : state.status === "ready" ? "重新生成" : state.status === "error" ? "重试生成" : "生成复盘总结";
  return <section className="review-overview" aria-label="AI短线总结" aria-busy={pending}>
    <div className="review-overview-heading">
      <div><h2>AI短线总结</h2><span>截至 {endDate} · 市场延续 / 候选晋级 / 三板以上</span></div>
      <button type="button" disabled={pending || loading || !report} onClick={() => void resource.generate(endDate)}>
        {pending ? <LoaderCircle size={15} className="state-spinner" />
          : state.status === "ready" ? <RefreshCcw size={15} /> : <Sparkles size={15} />}{label}
      </button>
    </div>
    {state.status === "idle" ? <p className="review-overview-hint">点击生成本期 300–400 字总结。</p> : null}
    {pending ? <p className="review-overview-status" role="status">{state.status === "checking" ? "正在核对事实与总结内容" : "正在生成，内容待核验"}</p> : null}
    {state.text ? <div className="review-overview-prose" aria-label="整体表现">
      <ReactMarkdown skipHtml allowedElements={["p", "strong", "em", "h3", "h4", "ul", "ol", "li", "br"]} unwrapDisallowed>{state.text}</ReactMarkdown>
    </div> : null}
    {state.status === "error" ? <p className="review-agent-error" role="alert">{state.error}</p> : null}
    {state.status === "ready" && state.model ? <p className="review-overview-status">AI 总结 · {state.model}</p> : null}
  </section>;
}

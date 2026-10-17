import type { EvaluationHistory } from "../utils/adminQuality";

const dimensions: Record<string, string> = {
  intent_delivery: "意图与交付", factual_grounding: "事实接地",
  source_attribution: "来源归属", investment_boundary: "投资表达边界",
};
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" ? value : "";

function Findings({ evidence }: { evidence: unknown }) {
  const findings = object(evidence).findings;
  if (!Array.isArray(findings)) return null;
  return <>{findings.map((value, index) => {
    const finding = object(value);
    const answer = object(finding.answer_quote), requirement = object(finding.requirement_quote);
    return <div className="quality-judge-finding" key={index}>
      {text(answer.text) ? <p><strong>回答原句：</strong><q>{text(answer.text)}</q><small>引用 {text(answer.ref_id)}</small></p> : null}
      {text(requirement.text) ? <p><strong>对应要求：</strong><q>{text(requirement.text)}</q><small>引用 {text(requirement.ref_id)}</small></p> : null}
      {text(finding.summary) ? <p><strong>判断理由：</strong>{text(finding.summary)}</p> : null}
      {finding.references ? <details><summary>对应证据</summary><pre>{JSON.stringify(finding.references, null, 2)}</pre></details> : null}
    </div>;
  })}</>;
}

export function QualityEvaluationReports({ evaluations }: { evaluations: EvaluationHistory | null }) {
  return <section aria-label="机器评审报告">
    <h3>机器评审报告（{evaluations?.total ?? 0}）</h3>
    <p>通过仅代表所列检查范围；未知表示材料不足或不适用。评审执行失败不代表回答错误，语义问题仍需人工复核。</p>
    {evaluations && evaluations.items.length < evaluations.total ? <p>当前展示最近 {evaluations.items.length} 份报告。</p> : null}
    {evaluations?.items.map(evaluation => {
      const semantic = evaluation.evaluator_kind === "llm_judge";
      const label = evaluation.execution_status !== "succeeded" ? "尚无有效评审结论" : evaluation.verdict === "fail"
        ? semantic ? "疑似语义问题" : "发现明确不一致"
        : evaluation.verdict === "pass" ? "所列维度通过" : "检查范围未完全确认";
      return <article className="quality-signal" key={evaluation.evaluation_id}>
        <strong>{semantic ? "LLM Judge" : "确定性检查"} · {label}</strong>
        <small>评审执行：{({ running: "执行中", succeeded: "完成", error: "失败", skipped: "跳过" })[evaluation.execution_status]} · {evaluation.evaluator_version}</small>
        <small>证据包：{evaluation.bundle_id ?? "历史任务未留存"}</small>
        {evaluation.report?.checks?.map(check => <div key={check.check_id}>
          <p><strong>{dimensions[check.dimension ?? ""] ?? check.dimension ?? check.check_id}</strong> · {({ pass: "通过", fail: semantic ? "待人工复核" : "失败", unknown: "未知" })[check.verdict]} · {check.summary}
            {check.unknown_reason ? `（${check.unknown_reason}）` : ""}</p>
          <Findings evidence={check.evidence} />
        </div>)}
        <details><summary>检查定位与执行详情</summary><pre>{evaluation.report ? JSON.stringify(evaluation.report, null, 2) : "评审尚未完成"}</pre></details>
      </article>;
    })}
  </section>;
}

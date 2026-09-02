import { ChevronDown, ExternalLink, GitBranch, ShieldAlert } from "lucide-react";

import type {
  AgentChatPerformance,
  AgentEvidenceCard,
  AgentToolPolicyAudit,
} from "../types";

interface AgentAnswerEvidenceMessage {
  evidenceCards?: AgentEvidenceCard[];
  toolPolicy?: AgentToolPolicyAudit;
  references?: string[];
  warnings?: string[];
  performance?: AgentChatPerformance;
}

const evidenceMetricLabels: Record<string, string> = {
  trade_date: "交易日",
  data_as_of: "数据截止",
  captured_at: "采集时间",
  fetched_at: "获取时间",
  source: "数据源",
  sector_name: "板块",
  sector_count: "板块数",
  member_count: "成分数",
  analyzed_count: "已分析",
  missing_count: "缺失数",
  candidate_count: "候选数",
  matched_count: "命中数",
  stock_count: "股票数",
  event_count: "事件数",
  first_board_count: "首板数",
  continued_board_count: "连板数",
  failed_count: "炸板数",
  limit_up_count: "涨停数",
  score: "评分",
  rating: "评级",
  confidence: "置信度",
  status: "状态",
};

export function AgentAnswerEvidence({
  message,
}: {
  message: AgentAnswerEvidenceMessage;
}) {
  const cards = (message.evidenceCards ?? []).filter((card) => card.kind !== "execution");
  const warnings = [...new Set(message.warnings ?? [])];
  const policy = message.toolPolicy;
  const executedToolCount = policy?.final_tool_calls.length ?? 0;
  const repairCount = policy?.backend_repaired_tools.length ?? 0;
  const durationMs = message.performance?.total_duration_ms ?? 0;
  const externalReferences = (message.references ?? []).filter((item) => /^https?:\/\//i.test(item));
  const hasEvidence = cards.length > 0 || warnings.length > 0 || executedToolCount > 0;

  if (!hasEvidence) {
    return null;
  }

  const summaryParts = [
    cards.length > 0 ? `${cards.length} 组证据` : null,
    executedToolCount > 0 ? `${executedToolCount} 项数据核验` : null,
    durationMs > 0 ? `${(durationMs / 1000).toFixed(1)}s` : null,
  ].filter(Boolean);

  return (
    <details className="agent-answer-evidence">
      <summary>
        <span><GitBranch aria-hidden="true" size={14} />查看回答依据</span>
        <small>{summaryParts.join(" · ")}</small>
        <ChevronDown aria-hidden="true" className="evidence-chevron" size={15} />
      </summary>
      <div className="agent-evidence-panel">
        {policy && executedToolCount > 0 ? (
          <div className={`agent-evidence-verification ${repairCount > 0 ? "repaired" : "verified"}`}>
            <strong>{repairCount > 0 ? "证据计划已补全" : "证据计划已校验"}</strong>
            <span>
              {repairCount > 0
                ? `后端补充了 ${repairCount} 项必要数据，避免仅凭模型回答。`
                : `本次回答基于 ${executedToolCount} 项已执行数据查询。`}
            </span>
            {policy.repair_reasons.length > 0 ? (
              <ul>{policy.repair_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            ) : null}
          </div>
        ) : null}

        {warnings.length > 0 ? (
          <div className="agent-answer-warnings">
            <ShieldAlert aria-hidden="true" size={15} />
            <div>
              <strong>数据边界与风险提示</strong>
              {warnings.map((warning) => <span key={warning}>{warning}</span>)}
            </div>
          </div>
        ) : null}

        {cards.length > 0 ? (
          <div className="agent-evidence-cards">
            {cards.map((card, index) => {
              const facts = card.facts.filter((fact) => fact !== card.summary).slice(0, 4);
              const metrics = Object.entries(card.metrics).slice(0, 6);
              return (
                <article
                  className={`agent-evidence-card evidence-${card.status}`}
                  key={`${card.title}-${index}`}
                >
                  <header>
                    <strong>{card.title}</strong>
                    <span>{card.status === "success" ? "已核验" : card.status === "error" ? "失败" : "受限"}</span>
                  </header>
                  <p>{card.summary}</p>
                  {metrics.length > 0 ? (
                    <div className="agent-evidence-metrics">
                      {metrics.map(([key, value]) => (
                        <span key={key}>
                          <small>{evidenceMetricLabels[key] ?? key}</small>
                          <strong>{formatEvidenceValue(value)}</strong>
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {facts.length > 0 ? <ul>{facts.map((fact) => <li key={fact}>{fact}</li>)}</ul> : null}
                </article>
              );
            })}
          </div>
        ) : null}

        {externalReferences.length > 0 ? (
          <div className="agent-evidence-links">
            {externalReferences.map((reference, index) => (
              <a href={reference} key={reference} rel="noreferrer" target="_blank">
                <ExternalLink aria-hidden="true" size={13} />来源 {index + 1}
              </a>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}

function formatEvidenceValue(value: string | number | boolean | null) {
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  if (value === null || value === "") {
    return "—";
  }
  return String(value);
}

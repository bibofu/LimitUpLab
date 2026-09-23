import { LoaderCircle } from "lucide-react";
import { memo, useEffect, useState } from "react";

import type { AgentChatStreamStage } from "../types";

const STAGE_LABELS: Record<AgentChatStreamStage, string> = {
  preparing: "准备查询",
  planning: "分析问题",
  tools: "查询数据",
  checking: "核对回答",
  answering: "显示结果",
};

/** Keep the running clock independent of the conversation/Markdown render tree. */
export const AgentChatProgress = memo(function AgentChatProgress({
  stage,
  message,
  startedAt,
}: {
  stage: AgentChatStreamStage;
  message: string;
  startedAt: number;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="chat-progress">
      <LoaderCircle aria-hidden="true" size={18} />
      <div>
        <div className="chat-progress-heading">
          <strong>{STAGE_LABELS[stage]}</strong>
          <span aria-hidden="true">已用时 {Math.max(0, Math.floor((now - startedAt) / 1000))} 秒</span>
        </div>
        <span role="status">{message}</span>
      </div>
    </div>
  );
});

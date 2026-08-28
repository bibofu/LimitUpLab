# 项目协作约定

## 项目定位

LimitUpLab 是一个面向 A 股收盘后短线研究的涨停与首板复盘系统。项目基于真实涨停、炸板、K 线、板块、人气、龙虎榜等数据，构建可解释的首板评级、历史追踪和工具型 Agent 问答闭环。

本项目用于数据研究、Agent 工程实践和作品展示，不构成投资建议。系统不得输出买入、卖出、仓位、目标价、收益承诺等结论。

## 主要功能

- 涨停、炸板、K 线、板块、人气、龙虎榜等数据采集与本地持久化。
- 首板候选过滤、结构化 Facts 构建、规则评分、评级、置信度和风险解释。
- 每日 Top10 预测快照、D+1 至 D+5 走势追踪、1 进 2 对照和评分复盘。
- Tool-Using Chat Agent：通过 Planner、Tool Policy、结构化工具和 SSE 流式回答完成事实接地问答。
- Explanation、Critic、Review、Evaluation 等轻量 Agent 角色，用于解释、质疑、追踪和复盘。
- Champion/Challenger 评分策略管理、影子优化、预测质量审计和数据健康检查。
- React 前端工作台、FastAPI 后端接口、SQLite 本地数据和 Docker/Nginx 部署基线。

## 技术栈

- 后端：Python 3.13、FastAPI、Pydantic、Uvicorn、SQLite、pytest。
- 数据：AKShare、同花顺相关数据源、东方财富/公开资讯检索，以及本地派生特征与缓存表。
- Agent：结构化工具注册、Tool Policy Engine、LLM Planner、Facts-first 回答链路和可降级模板答案。
- 前端：React 19、TypeScript、Vite、React Router、lightweight-charts、lucide-react、react-markdown。
- 部署：Docker Compose、Nginx、Windows Task Scheduler/脚本化日更任务。

## 必要规范

- 事实优先：涉及市场、个股、评分、复盘和统计时，必须基于结构化数据、工具结果或明确来源，不得凭记忆补造数字。
- 投资合规：回答和界面文案只能做研究、解释和风险提示，不输出交易指令、收益承诺或确定性预测。
- 评分可回放：评分逻辑、版本、输入 Facts、缺失字段和置信度应保持可测试、可追踪、可复盘。
- 数据缺失显式化：缺失数据写入 `data_missing` 或对应健康检查结果，不用默认值掩盖关键缺口。
- LLM 有边界：LLM 负责规划、解释、总结和追问；原始分数、关键过滤和可审计统计优先由确定性代码完成。
- 工具调用受控：新增 Agent 工具时同步考虑 Query Contract、Tool Policy、测试覆盖、错误返回和 trace 摘要。
- 前端展示克制：研究型工作台优先信息密度、可扫描性和证据链，不做营销式页面或过度装饰。
- 测试随风险扩展：修改共享服务、评分策略、数据管线、Agent 工具或用户可见流程时补充或更新对应 pytest/前端构建验证。
- 配置与密钥安全：真实 API Key、生产 `.env`、本地数据库和生成报告不得提交；示例配置使用 `.example` 文件。
- 数据流水线谨慎：涉及历史预测快照、Outcome 回填和 Champion/Challenger 的改动，不得破坏不可变预测记录或把回测样本伪装成 live 结果。
- Git 约定：每完成一次代码或文档修改，就执行一次 git commit；提交时只纳入本次任务相关文件，不夹带无关本地数据或用户改动。

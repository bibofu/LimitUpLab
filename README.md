# LimitUpLab

基于真实 A 股涨停数据的可解释首板评级与复盘 Agent。

LimitUpLab 面向收盘后的短线研究场景：系统从当日涨停股票中筛选合格首板，构建结构化事实，生成可解释评分，并通过 LLM Agent 回答用户对市场、板块、个股和历史表现的追问。

> 本项目用于数据研究、Agent 工程实践和求职展示，不构成任何投资建议。系统不提供买入、卖出、仓位、目标价或收益承诺。

## 项目状态

`v1.0.0` 是首个可用基线，定义为基于完整收盘数据的首板复盘与次日一进二候选研究工具。`v1.1.0` 增加了双策略盘前研究和集合竞价实验。当前主线已移除集合竞价运行链路：每天收盘后固化一进二 Top10 并进入复盘；另一条研究链路改为“低位挖掘”，从热门题材与最新催化出发，结合财报和 60 日 K 线筛选低位启动观察池，不再输出“次日首板 Top10”。集合竞价实验的代码固定在 `v1.1.0` 标签，设计记录见 [V1.1 阶段里程碑](docs/V1.1_Milestone.md)。

当前版本已经具备可本地运行和单机部署的完整 MVP：

- 真实涨停、炸板、K 线、板块、人气和龙虎榜数据流水线
- 首板候选池过滤、结构化 Facts、规则评分和置信度
- 每日 Top10 推荐快照及 D+1 至 D+5 走势追踪
- 原生 Function Calling Planner、Tool Policy 修复和 SSE 流式回答
- 可恢复的多会话对话、滚动 Session Memory 和受控上下文
- Explanation、Critic、Review、Evaluation 等轻量 Agent 角色
- 每日 Top10 预测快照、D+1 至 D+5 走势追踪、1进2全市场对照和不可变每日复盘快照
- Champion/Challenger 评分策略注册与受约束影子优化
- 来源感知的预测质量审计、确定性基线和多目标评分 v3
- 每日连板晋级率、首板到二板率和各板高梯队统计
- 数据健康、Agent 运行轨迹、缓存和离线回归评测
- 低位挖掘观察池与一进二接力 Top10 两条边界清晰的研究链路

当前代码基线：

| 项目 | 状态 |
| --- | --- |
| 后端自动化测试 | 334 项通过，另有 10 个参数化子测试 |
| 离线 Agent Eval | Core 18/18、Query Contract 36/36 通过 |
| 本地数据健康检查 | 已实现 |
| LLM 流式问答 | 已实现 |
| Agent 限流与成本审计 | 单访客/IP/全局限制、真实 token 账本已实现 |
| 评分 v3 工程实现 | 已完成，影子验证中 |
| 单机生产部署基线 | Docker、Nginx、HTTPS、持久化卷、备份和日更任务已部署 |

评分结果目前仍处于研究和验证阶段。项目已经形成完整的数据与评价闭环，但尚未证明可以稳定预测次日表现。

## 为什么做这个项目

传统涨停看板通常只展示行情，普通股票聊天机器人则容易脱离数据泛泛而谈。LimitUpLab 尝试解决两者之间的问题：

```text
真实数据
  -> 首板候选过滤
  -> 可解释评分
  -> 不可变 Top10 预测快照
  -> LLM 工具调用问答
  -> Critic 质疑
  -> 结果回填
  -> Evaluation 复盘
  -> 评分策略改进建议
```

这个项目的核心不是让 LLM 直接预测股票，而是让 LLM 在确定性数据和工具之上完成规划、解释、追问、批判和复盘。

## 核心能力

### 1. Facts-first 首板评分

系统先构建结构化事实，再由版本化评分策略计算分数。LLM 不参与原始分数计算，因此每个结论都可以测试和回放。

候选池默认：

- 只保留收盘封住的首板股票
- 一进二接力排除 ST、北交所、科创板和创业板
- 排除新股与次新股
- 排除成交额过小样本
- 缺失字段显式写入 `data_missing` 并降低置信度

当前评分策略包含 14 类输入：首封时间、上板形态、封板稳定性、封板次数、换手率、成交额、行业热度、市场环境、涨停前 K 线结构、板块接力、市值偏好、近期股性、龙虎榜和人气快照。

每只候选都会返回：

```text
score
rating
confidence
score_breakdown
reasons
risks
data_missing
scoring_version
```

其中 `score` 表示候选强度，`confidence` 表示当前数据对该评分的支持程度，两者不会混为一谈。

### 1.1 低位挖掘观察池

低位挖掘不预测“明天哪只股票会首板”，也不固定输出 Top10。系统先从最新热门概念和行业及其新闻催化中召回股票，再要求至少 60 根日 K，剔除近期涨幅过大、接近 60 日高位和二波高位结构的样本，最后用以下证据形成可解释研究排序：

1. **题材**：所处热门概念或行业、板块涨幅与市场关注度。
2. **新闻和财报**：近 48 小时题材催化、近 7 日个股新闻和最新季度财报同比变化。
3. **走势**：近 5/20/60 日收益、60 日区间位置、距高点距离、量比、均线和收盘强度。

前端与 Agent 都按这三类证据展示候选。该观察池只用于研究可能处于趋势启动早期的标的，不表示主升浪概率，也不进入一进二 Top10 的预测复盘口径。

### 2. Tool-Using Chat Agent

正常问答主链路是：

```text
用户问题
  -> LLM Planner 原生调用 submit_agent_plan
  -> Agent Query Contract v3 归一化业务能力和多轮上下文关系
  -> Query Contract v2 统一日期、市场、板高、状态、排序和数量
  -> Tool Policy Engine 按能力契约检查事实接地要求
  -> 后端执行结构化工具
  -> LLM 只基于工具 Facts 生成回答
  -> SSE 流式返回文本和执行轨迹
```

当前主要工具：

| 工具 | 用途 |
| --- | --- |
| `market_summary` | 查询指定交易日市场环境 |
| `market_index_trend` | 查询上证、深成指和创业板指近 2–20 个交易日走势 |
| `dragon_tiger_list` | 查询同花顺龙虎榜、机构与游资净买额 |
| `first_board_ratings` | 查询首板评级、拆解和风险 |
| `first_board_discovery` | 查询低位挖掘观察池，并按题材、新闻财报和走势解释 |
| `first_board_filter` | 按行业、题材、评级和置信度筛选 |
| `limit_up_events` | 查询首板、连板、炸板及题材相关涨停事件 |
| `daily_board_promotion` | 统计近 5 日总晋级率、首板到二板率、连板梯队及成功股票明细 |
| `stock_kline` | 查询个股近期 K 线和衍生指标 |
| `stock_news` | 查询指定股票近 1 至 30 天的结构化个股资讯，保留来源、时间和链接 |
| `stock_activity` | 汇总指定股票的收盘走势、近期涨停记录、评分补充事实和个股资讯 |
| `first_board_critic` | 复核评分是否过度乐观或证据不足 |
| `rating_backtest` | 查看评分分桶历史表现 |
| `rating_evaluation` | 评价历史预测结果和错误样本 |
| `review_high_score_picks` | 追踪每日评分 Top10 后续走势，并对比同期全部首板的1进2成功率 |
| `prediction_quality_audit` | 审计预测来源、Outcome 覆盖和基线表现 |
| `scoring_policy_status` | 查询 Champion、Challenger 和晋级原因 |

默认配置 `LIMITUPLAB_AGENT_PROFILE=v1_close_review` 会在 Planner Schema、Capability Contract、Tool Policy 和执行器四层统一限制工具。Capability 是业务工作流的单一声明源，同时定义示例问法、最低证据工具、默认参数和按需回答规范；系统不再维护重复的运行时 Skill Registry。V1 允许按需读取带来源和采集时间的 `hot_stock_ranking` 热度快照、`sector_performance` 行业强弱榜、`finance_news` 综合财经快讯，以及带 SQLite 缓存的 `stock_news`、`stock_activity` 个股资讯与收盘后动态；这些外部事实不参与首板评分，也不被解释为推荐。`remote_limit_up_pool` 和 `web_search` 仅保留在 `extended` 研发配置中，供 V2 能力开发使用。即使 LLM 伪造这些未开放工具调用，V1 执行器也会拒绝执行。

Agent Query Contract v3 先把不同说法归一为稳定能力 ID，例如 `market_environment`、`limit_up_pool`、`first_board_rating` 和 `prediction_review`。Planner 通过 OpenAI-compatible `tools` 与强制 `tool_choice` 原生调用 `submit_agent_plan`，由函数参数承载能力、上下文关系和证据工具计划，不再依赖模型在文本中手写 JSON；不支持 Function Calling 的兼容 Provider 可回退到 Prompt-to-JSON。组合问题可以选择多个能力；多轮追问通过 `standalone`、`entity_followup`、`source_refinement` 区分独立问题、实体继承和上一轮结果集交叉查询，并只继承 Planner 明确选择的 `context_capabilities`。有显式能力契约时，旧关键词判断不再参与语义路由，只在旧 Provider 或 Planner 未输出能力时兜底。

Query Contract v2 继续负责涨停查询中的确定性参数：用户明确表达的日期、首板/连板、主板/创业板/科创板/北交所、封板/炸板、题材、排序、Top-N 和完整名单要求优先于 Planner 猜测。Tool Policy Engine 根据 v3 能力补齐所需证据工具，不能让模型直接凭记忆回答市场事实。

### 2.1 Session Memory

长对话不再只依赖固定截断。系统保留最近 8 条原始消息；消息离开该窗口后，每累计 8 条便通过原生 `update_session_memory` Function Call 更新一次 SQLite 滚动记忆。记忆包含会话摘要、研究目标、股票实体、日期范围、用户约束和未解决问题，并与尚未摘要的消息及最近窗口一起交给 Planner 和最终回答。

Memory 按 `owner_id + session_id` 隔离，使用 `last_message_id` 作为增量游标，随会话永久删除。Provider 不支持 Function Calling 时可回退到 JSON 摘要，LLM 完全不可用时使用确定性压缩，因此 Memory 故障不会阻断正常问答。记忆只用于对话连续性，不能作为股价、新闻、评分、排名或市场状态的证据；所有时效性事实仍必须重新调用工具。当前实现是会话级 Memory，不会跨会话建立用户画像。

### 3. 轻量 Multi-Agent 角色

项目采用有边界的角色编排，不让多个 LLM 无约束地互相讨论。

| 角色 | 职责 | 是否直接使用 LLM |
| --- | --- | --- |
| Coordinator / Chat Agent | 理解问题、规划工具、组织回答 | 是 |
| Facts Builder | 从数据库构建结构化事实 | 否 |
| Rating Engine | 计算评分、评级和置信度 | 否 |
| Explanation Agent | 基于 Facts 解释评分与风险 | 是，可降级 |
| Critic Agent | 查找反向证据和数据缺口 | 结构化规则为主 |
| Review Agent | 追踪每日 Top10 后续走势及1进2全市场对照 | 可选 LLM 总结 |
| Evaluation Agent | 对历史预测分类并生成经验教训 | 结构化结果为主 |

每个角色都有结构化输入输出，可以独立测试、缓存、追踪和回放。

### 4. 自我评价与受控改进

系统每天保存不可变的评分预测快照，并在后续交易日回填：

- 次日开盘、最高、最低和收盘表现
- 是否晋级二板
- 以次日开盘为基准的收益和回撤
- 三日表现和 D+1 至 D+5 K 线

Evaluation Agent 将历史预测标记为 `success`、`partial`、`miss`、`avoid_success` 或 `false_negative`，并生成评分因子改进建议。

每日 live Top10 以整批快照写入 `agent_live_prediction_snapshots`。同一交易日首次写入后不可被重复日更或新评分版本覆盖；推荐页面、Agent、Review、Evaluation 和质量审计读取同一批候选，历史重算结果不能混入真实前向预测。

Outcome 完整性检查严格按本地市场交易日对齐 D+1、D+3 和 D+5。缺少某一交易日 K 线时不会使用下一根可用 K 线替代；缺失股票会进入日更报告、数据健康和 Review 告警，并在 20 个交易日重试窗口内继续追补。

评分策略采用 Champion/Challenger 管理：

- 因子权重具有明确版本
- 训练、验证和测试按交易日期顺序执行扩展窗口 walk-forward，测试日不重叠
- 以 Top10 相对同期全部首板的一进二提升为主目标，次日收益、大跌率和三日回撤作为保护约束
- 因子相关性按交易日去中心化后计算，减少不同市场环境造成的截面偏差
- 单次权重变化受到限制
- Challenger 默认只在影子模式运行
- Challenger 必须同时优于现行策略，并接受最早封板、固定随机和 Outcome 可用候选池基线审计
- 只有至少 60 个结果日、足够的样本外折和 Top10 样本且无关键风险退化时，显式批准后才能启用

这不是无约束的“模型自动学习”。当前系统只生成和验证候选策略，不会因为一次复盘就自动修改线上规则。

`/api/agents/prediction-quality-audit` 会先按 `live / historical_backtest` 和评分版本拆分预测，再按交易日选择完整批次；未成熟、Outcome 待回填和完整样本分开统计，避免把未来尚不存在的结果算成失败，也避免把历史重算样本混入真实当日 Top10。

`/api/agents/factor-signal-diagnostic` 提供独立的快速证伪诊断：单因子按交易日计算横截面 IC，并使用日期级符号翻转检验和 Bonferroni 校正；分位比较保留同分样本；联合 Lasso 使用按交易日留一的样本外预测和日期块 bootstrap。该报告只输出“当前未发现可复现信号”或“信号需要继续验证”，不会把小样本下的未显著误写成“因子已被证明为噪声”。

`/api/agents/scoring-error-diagnostic` 从结果完整日期中识别 Top10 高分误选和 Top10 之外的晋级漏选，并对 14 个评分因子逐一做排序消融。诊断只提出“观察上调、观察下调或暂不调整”的影子假设；样本不足、Outcome 不完整或未通过 walk-forward 门槛时不会改写 Champion。

截至 2026-08-22 的本地审计中，v3 已生成 3 个测试日不重叠的样本外折，但只有 14 个 Outcome 结果日，因此仍保持影子状态。当前评分 Top10 尚未优于最早封板基线，页面会如实展示这一结论和数据覆盖缺口。

### 5. 可观测与可降级

- `agent_runs` 保存每次 Agent 执行状态、输入、输出、错误和耗时
- `agent_usage_events` 保存已接受和被拒绝的请求、真实 token、显式价格下的估算成本及耗时
- `chat_sessions`、`chat_messages` 和 `chat_session_memories` 保存会话、原始消息与滚动记忆，支持新建、恢复、重命名和永久删除
- 页面刷新后恢复完整消息和证据卡；LLM 上下文由滚动摘要、尚未摘要消息和最近 8 条原始消息组成，仍保留硬上限
- Tool trace 展示 Planner 原始计划、后端修复原因、工具参数和结果摘要
- SQLite Agent Cache 缓存低风险结构化结果
- `/api/agents/data-health` 检查评分、预测追踪和 Outcome 所需数据
- `/api/agents/system-health` 检查数据新鲜度、LLM、代理和 Eval 状态
- LLM 不可用时回退到基于相同 Facts 的模板答案
- 工具失败时返回明确错误，不允许模型补造数字
- 匿名访客按分钟、按日限额，同一访客单并发，并设置单进程全局并发上限；超限统一返回 `429` 和 `Retry-After`

## 系统架构

```mermaid
flowchart LR
    A[AKShare / Tonghuashun / Eastmoney / CNInfo / Tencent / Sina] --> B[Daily Data Pipeline]
    B --> C[(SQLite)]

    C --> D[Facts Builder]
    D --> E[Rating Engine]
    E --> G[Prediction Snapshots]
    G --> H[Top10 Outcome Tracking]

    U[User] --> I[React Agent Workspace]
    I --> J[FastAPI Chat / SSE]
    J --> S[(Chat Sessions + Messages)]
    J --> K[LLM Planner]
    K --> L[Tool Policy Engine]
    L --> M[Tool Registry]
    M --> C
    M --> N[Sector / Web Search]
    M --> O[Critic / Review / Evaluation]
    M --> P[LLM Grounded Answer]
    P --> I

    G --> Q[Outcome Backfill]
    Q --> O
    O --> R[Champion / Challenger Suggestions]
```

### Agent 请求时序

```mermaid
sequenceDiagram
    participant User
    participant UI as React UI
    participant API as FastAPI
    participant Memory as Session Memory
    participant Planner as LLM Planner
    participant Policy as Tool Policy
    participant Tools as Tool Registry
    participant Answer as LLM Answer

    User->>UI: 输入自然语言问题
    UI->>API: POST /api/agents/chat/stream
    API->>Memory: 加载或增量更新滚动摘要
    Memory-->>API: 摘要 + 结构化状态 + 最近消息
    API->>Planner: 系统指令 + 工具 Schema + Memory 上下文
    Planner-->>API: submit_agent_plan Function Call
    API->>Policy: 校验事实接地和必需工具
    Policy-->>API: 最终工具计划 + 修复原因
    API->>Tools: 执行结构化查询
    Tools-->>API: Facts + References + Trace
    API->>Answer: 用户问题 + 工具 Facts
    Answer-->>UI: SSE answer_delta
    API-->>UI: completed + run_id + tool trace
```

## 技术栈

### Backend

- Python 3.13
- FastAPI + Uvicorn
- Pydantic
- SQLite
- AKShare
- Requests + BeautifulSoup
- DeepSeek 兼容 Chat Completions API

### Frontend

- React 19
- TypeScript
- Vite 6
- React Router
- React Markdown + GFM
- Lucide React

### Agent Engineering

- LLM Tool Planner
- 声明式 Tool Schema
- Tool Policy Repair
- Facts Grounding
- SSE Streaming
- Controlled Conversation Context
- Persistent Chat Sessions
- Agent Run Observability
- Deterministic + Live LLM Eval
- Champion/Challenger Policy Governance

## 项目结构

```text
LimitUpLab/
├── backend/
│   ├── app/
│   │   ├── agents/          # Chat、Explanation、Review、Eval 和 Tool Policy
│   │   ├── collectors/      # 涨停、K 线、板块、指数和扩展数据采集
│   │   ├── repositories/    # SQLite Repository
│   │   ├── routers/         # FastAPI 路由
│   │   └── services/        # 评分、检索、回测、Evaluation 和健康检查
│   ├── scripts/             # 数据同步、回填、Eval 和启动脚本
│   └── tests/               # 单元测试与 Agent Eval 数据集
├── frontend/
│   └── src/                 # React 工作台、API 类型和样式
├── scripts/                 # 项目级本地启动脚本
└── docs/
    ├── Tasks.md             # 开发路线图和进度
    └── 需求.md              # 完整产品与 Agent 需求
```

## 快速开始

### 环境要求

- Python 3.13，Windows 下不建议使用尚未完全兼容依赖的 Python 3.14
- Node.js 18+
- npm
- 能访问所配置行情源和 LLM 服务的网络环境

### 1. 安装后端

Windows PowerShell：

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux：

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. 配置 LLM

编辑 `backend/.env`：

```dotenv
LIMITUPLAB_LLM_ENABLED=true
LIMITUPLAB_LLM_BASE_URL=https://api.deepseek.com
LIMITUPLAB_LLM_MODEL=deepseek-v4-flash
LIMITUPLAB_LLM_THINKING_ENABLED=false
LIMITUPLAB_LLM_NATIVE_FUNCTION_CALLING=true
DEEPSEEK_API_KEY=<your-api-key>
LIMITUPLAB_SESSION_MEMORY_ENABLED=true
LIMITUPLAB_SESSION_MEMORY_REFRESH_MESSAGES=8
```

真实 API Key 不应提交到 Git。未配置 LLM 时，结构化评分、检索和模板回答仍然可以运行。

公开运行时建议同时配置 Agent 容量。默认每访客 8 次/分钟、60 次/天、同访客 1 个并发、全站 4 个并发。管理接口 `GET /api/agents/usage?days=7` 可查看请求、拒绝、LLM 调用和 token 汇总，需要 `X-LimitUpLab-Admin-Key`。

```dotenv
LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED=true
LIMITUPLAB_AGENT_REQUESTS_PER_MINUTE=8
LIMITUPLAB_AGENT_REQUESTS_PER_DAY=60
LIMITUPLAB_AGENT_IP_REQUESTS_PER_MINUTE=20
LIMITUPLAB_AGENT_MAX_CONCURRENT_PER_USER=1
LIMITUPLAB_AGENT_MAX_CONCURRENT_GLOBAL=4
```

如需记录估算金额并启用每日成本上限，再按当前模型官方价格填写每百万 token 单价。价格未配置时仍记录供应商返回的真实 token，但成本保持为 0，不会使用不可靠的字符数估算。

### 3. SQLite 单机并发配置

所有仓储统一通过数据库连接层启用 WAL、外键约束、5 秒忙等待、NORMAL 同步级别和有界锁冲突重试。Schema 使用 `PRAGMA user_version` 管理，并在 `BEGIN IMMEDIATE` 事务中只初始化一次，普通读请求不再重复执行整套 DDL 和修复写入。

```dotenv
LIMITUPLAB_SQLITE_WAL_ENABLED=true
LIMITUPLAB_SQLITE_BUSY_TIMEOUT_MS=5000
LIMITUPLAB_SQLITE_LOCK_RETRY_ATTEMPTS=3
LIMITUPLAB_SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS=0.05
LIMITUPLAB_SQLITE_WAL_AUTOCHECKPOINT_PAGES=1000
```

该配置适合单机 Docker Volume。数据库繁忙超过等待与重试上限后仍会明确失败，不会无限阻塞请求；需要多实例写入时应迁移 PostgreSQL，不能通过共享 SQLite 文件替代。

如需本地代理：

```dotenv
LIMITUPLAB_PROXY_URL=http://127.0.0.1:17891
```

### 4. 配置同花顺结构化数据

项目通过官方 `hithink-finance` CLI 获取热股榜、龙虎榜和远端涨停池。凭据保存在 CLI 系统凭据库，不写入项目 `.env`：

```powershell
npm.cmd install -g @hithink-tech/hithink-finance-cli
hithink-finance auth login
hithink-finance symbol search --q 600519 --limit 1 --format json
```

CLI 不可用或请求失败时，每日 enrichment 会回退到原有 AkShare/东方财富来源。V1 Agent 不暴露同花顺实时工具；这些工具仅在 `extended` 研发配置中用于 V2 开发，请求失败时会明确返回错误，不会伪造结果。

### 5. 安装前端

```powershell
cd frontend
npm.cmd install
```

macOS / Linux 使用：

```bash
cd frontend
npm install
```

### 6. Windows 一键启动

在项目根目录执行：

```powershell
.\scripts\start_local.cmd
```

脚本会先检查最新完整交易日和 Agent 数据健康状态；本地数据过期时会尝试执行日更，再启动前后端。

- 前端：<http://127.0.0.1:5173>
- 后端 API：<http://127.0.0.1:8001>
- Swagger：<http://127.0.0.1:8001/docs>

### 7. 手动启动

后端：

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

前端：

```powershell
cd frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

macOS / Linux 将 Python 和 npm 命令替换为对应虚拟环境命令即可。

### 8. 香港服务器 Docker 部署

仓库已提供面向单机公开 Demo 的生产部署基线：

- `backend/Dockerfile`：FastAPI、Python 数据依赖和可选同花顺 CLI
- `frontend/Dockerfile`：React 生产构建与容器内 Nginx
- `docker-compose.yml`：健康检查、自动重启、SQLite 命名卷、日更 Job 和推荐情报半小时刷新服务
- `deploy/nginx/`：同域 API 反代、SPA 路由和 SSE 流式配置
- `.env.production.example`：不含真实密钥的生产环境模板

服务器上执行：

```bash
cp .env.production.example .env.production
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d
curl --fail http://127.0.0.1:8080/health
```

完整的域名、HTTPS、定时任务、数据迁移、备份和回滚步骤见 [`deploy/README.md`](./deploy/README.md)。当前配置有意保持单个 Uvicorn worker，以匹配 SQLite 单机阶段；正式多实例并发前应迁移 PostgreSQL 和 Redis。

## 每日数据更新

正常使用应执行完整流水线，而不是只导入一张涨停表：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\update_daily_data.py --date 20260821 --replace-date
```

流水线会依次处理：

1. 导入涨停与炸板事件，并用同花顺涨停池校验数量差异。
2. 重建近期首板结构化特征。
3. 获取 K 线、上市日期、流通市值、龙虎榜和人气快照；同花顺优先，原有来源回退。
4. 对最新完整交易日固化收盘 Top10，历史补算继续标记为 `historical_backtest`。
5. 回填近期 Top10 的 D+1 至 D+5 走势。
6. 输出 JSON 数据健康报告。

盘前推荐中的两类 Top30 候选共用动态草稿流水线。它每 30 分钟批量更新
最新行情、个股新闻、龙虎榜和人气 Top100，并读取同花顺最新季度利润表；
财报缓存 24 小时，避免对不会频繁变化的数据重复请求。收盘基础分保持可
追溯，盘后新增公告、新上龙虎榜和可比较的人气变化以有边界的修正更新研究
分与名次；复盘仍以收盘时已经固化的 Top10 为准，不会改写历史预测：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_recommendation_refresh_loop.py --once
```

本地一键启动和 Docker Compose 会自动启动持续刷新进程。刷新间隔可通过
`LIMITUPLAB_RECOMMENDATION_REFRESH_MINUTES` 配置，默认值为 `30`。

启动演示前也可以让系统自动判断预期交易日：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\dev_check.py --ensure-data
```

### 每日预测闭环自动化

Windows 本地环境可以安装收盘后的计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\daily_close_loop_task.ps1 -Mode Install
```

任务默认在每个工作日 `16:10` 执行，并启用错过后补跑、失败重试和禁止并发执行。一次执行会自动完成交易日判断、原始数据同步、首板特征与 enrichment、收盘 Top10 固化、盘前研究候选更新、历史 Top10 的 D+1 至 D+5 K 线缓存、Outcome 回填、每日复盘快照和健康检查。

预测来源有严格时间口径：当天完整收盘数据生成且固化的 Top10 标记为 `live`；事后补算只标记为 `historical_backtest`，不能混入真实前向预测准确率统计。盘前动态研究排序不覆盖收盘预测快照。

一进二动态研究排序使用明确的信息时点契约：原始规则分与截至基准交易日 `15:00` 已公开的新闻、财报共同构成收盘综合分；基础首板评分中已存在的龙虎榜和人气快照不会重复计分。动态修正只接受该时点后的新增公告、新上龙虎榜，以及相对基础快照可验证的人气名次变化；缺少人气基线时只展示最新排名并写入缺失项，不猜测涨跌。盘后各项修正合计限制在 `±6` 分，每只候选保留分项修正、动态合计和证据原因，便于回放。

手动执行、查看任务状态和卸载任务：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_daily_close_loop.py --trigger manual
cd ..
powershell -ExecutionPolicy Bypass -File .\scripts\daily_close_loop_task.ps1 -Mode Status
powershell -ExecutionPolicy Bypass -File .\scripts\daily_close_loop_task.ps1 -Mode Uninstall
```

每次执行都会写入 SQLite 审计记录。最新报告保存在 `backend/data/daily_close_loop_latest.json`，部分完成或失败时额外生成 `backend/data/daily_close_loop_alert.json`；前端系统状态条和 `GET /api/agents/daily-pipeline-status` 可查看最近运行结果。

## 常用 API

| 方法 | Endpoint | 说明 |
| --- | --- | --- |
| `GET` | `/api/agents/first-board-ratings` | 首板评级列表 |
| `GET` | `/api/agents/first-board-critic` | 单股 Critic 复核 |
| `GET` | `/api/agents/rating-backtest` | 评分分桶回测 |
| `GET` | `/api/agents/rating-evaluation` | Evaluation Agent 复盘 |
| `GET` | `/api/agents/review-report` | 近期 Top10 追踪报告 |
| `GET` | `/api/agents/review-snapshots` | 已固化每日复盘日期与摘要 |
| `GET` | `/api/agents/prediction-quality-audit` | 预测来源、覆盖率和基线审计 |
| `GET` | `/api/agents/factor-signal-diagnostic` | 日期阻断的因子快速证伪诊断 |
| `GET` | `/api/agents/scoring-error-diagnostic` | 高分误选、晋级漏选与逐因子消融 |
| `GET` | `/api/agents/scoring-policies` | Champion/Challenger 状态 |
| `GET` | `/api/agents/data-health` | Agent 数据健康 |
| `GET` | `/api/agents/outcome-completeness` | Top10 D+1、D+3、D+5 完整性验收 |
| `GET` | `/api/agents/system-health` | 本地系统健康 |
| `GET` | `/api/agents/daily-pipeline-status` | 每日预测闭环最近运行状态 |
| `GET` | `/api/agents/runs` | Agent 运行轨迹 |
| `POST` | `/api/agents/chat/sessions` | 创建新会话 |
| `GET` | `/api/agents/chat/sessions` | 查询可继续的会话列表 |
| `GET` | `/api/agents/chat/sessions/{session_id}` | 恢复会话消息和回答元数据 |
| `PATCH` | `/api/agents/chat/sessions/{session_id}` | 重命名会话 |
| `DELETE` | `/api/agents/chat/sessions/{session_id}` | 永久删除会话、消息和关联运行记录 |
| `POST` | `/api/agents/chat` | 同步 Agent 问答 |
| `POST` | `/api/agents/chat/stream` | SSE 流式 Agent 问答 |
| `GET` | `/api/limit-up/events` | 涨停事件查询 |
| `GET` | `/api/analysis/daily-promotion` | 每日连板晋级率与板高分层统计 |
| `GET` | `/api/stocks/{symbol}/kline` | 个股日 K 线 |

完整接口定义以 Swagger 为准。

## 测试与 Agent Eval

运行后端测试：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

运行确定性 Agent 回归和 Query Contract v2 回归：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py
```

运行 138 条自然语言改写的真实 Planner 三轮评测：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite paraphrase --mode live-llm --summary-only
```

运行携带生产消息结构和上一轮能力焦点的多轮评测：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite conversation --mode live-llm --summary-only
```

`paraphrase` 和 `conversation` 都只执行生产 Planner，不重复调用行情、新闻和 K 线工具；默认每条运行 3 次，从而把语义路由波动与外部数据源波动分开。可用 `--case-filter first_board_rating_05` 定向复测失败 case。报告分别输出能力命中率、有效工具命中率、case 通过率和三轮不稳定数量。

2026-08-30 的真实 DeepSeek 全量结果：121/121 单轮 case 达到最低通过门槛，363 次 Planner 调用的能力与工具命中率均为 99.72%，119 个 case 三轮完全一致；多轮场景 10/10 通过、60/60 turn 命中，9 个场景三轮完全一致。剩余波动会继续保留为模型稳定性治理对象，不用后端关键词规则掩盖。

需要抽查完整端到端回答时执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --suite core --mode live-llm --trials 1 --live-answer
```

CI 或发布前可以组合 `--fail-on-failures --fail-on-unstable` 使用严格门禁。Windows CLI 会读取 User/Machine 环境中的 API Key，并自动排除已知失效代理；LLM 对超时、连接失败、限流和服务端错误进行有限指数退避重试。

Eval 会检查：

- 意图和日期解析
- Query Contract 的市场、板高、事件状态、题材、排序、数量和参数优先级
- 必需与禁止调用的工具
- 最终工具参数、命中数量和股票集合
- Tool Policy 是否发生后端修复
- 回答是否包含关键事实
- 投资建议安全边界
- 工具调用失败和数据缺失时的表达
- 多次采样下的 Planner 一致性和真实 LLM 覆盖率

前端生产构建：

```powershell
cd frontend
npm.cmd run build
```

## 数据源与边界

当前数据来自官方同花顺 CLI、AKShare 封装或公开接口：

- 同花顺热股榜、龙虎榜和远端涨停池
- 东方财富涨停池和炸板池
- 同花顺与东方财富行业板块行情
- 腾讯、Sina、东方财富历史与分钟 K 线
- CNInfo 上市日期
- 东方财富龙虎榜和人气快照回退源
- 360 搜索、Bing 和 DuckDuckGo 的公开搜索结果回退链路

需要注意：

- 项目定位是收盘后复盘，不是实时交易终端。
- 免费公开数据源可能限流、延迟、变更字段或暂时不可用。
- 历史人气和龙虎榜时点数据无法在所有日期完整重建。
- Web 搜索内容被视为外部不可信证据，只用于解释增强，不直接决定评分。
- SQLite 适合本地 MVP，不代表正式多用户网站的最终存储方案。

## 当前限制

- 评分 v3 工程闭环已完成，但仍缺至少 60 个结果完整交易日的可靠样本外验证。
- 当前审计只有 8 个 Top10 次日 Outcome 完整日，覆盖率仍需要持续补齐。
- 当前预测准确性不高，不能宣称系统已经实现稳定选股。
- Query Contract v2 已覆盖涨停事件主链路，但其他工具仍需要逐步补齐同等级的结构化契约和更大规模真实 LLM Eval。
- 个股资讯已接入东方财富结构化搜索并持久化缓存；正式公告原文仍需补充交易所或巨潮资讯专用数据源。
- 当前限流适用于单 Uvicorn 进程；异步 Worker、跨实例 Redis 限流和上游 LLM 主动取消尚未完成。
- 用户系统、PostgreSQL、Redis、CI/CD 和多实例部署尚未完成；当前 Docker 配置适用于单机公开 Demo。

## Roadmap

近期优先级：

1. 滚动补齐 Top10 Outcome，将结果完整交易日从 14 个积累到至少 60 个。
2. 持续观察 v3 Challenger 对现行评分、最早封板和固定随机基线的样本外优势。
3. 完成 V1 Top10 不可变预测、D+1 晋级和 D+1 至 D+5 Outcome 的端到端验收。
4. 持续扩充 V1 收盘数据问答评测，覆盖工具失败、日期截止点和多轮指代。
5. V2 再为盘中板块、正式公告原文和更多策略建立独立数据契约与 Eval；需要横向扩容时迁移 PostgreSQL、Redis 限流和异步 Worker。

详细进度见 [Tasks.md](./docs/Tasks.md)，完整需求见 [需求.md](./docs/%E9%9C%80%E6%B1%82.md)。

## 面试演示建议

一条完整演示链路约 3 至 5 分钟：

1. 展示系统健康状态和最新交易日。
2. 查看当日首板评级 Top10 和评分拆解。
3. 打开一个候选，查看封板事实、K 线、当前位置和 Critic 复核。
4. 在 Agent 中询问“为什么这只股票评分高，有哪些反向风险”。
5. 展开工具轨迹，说明 LLM Planner、Tool Policy 和 Facts Grounding。
6. 查看过去五个交易日 Top10 的后续走势与 Evaluation 结果。
7. 展示 Champion/Challenger 未自动晋级的原因，说明系统如何避免无约束自我修改。

LimitUpLab 的重点不是功能数量，而是把数据、评分、检索、对话、自检、结果回填和受控改进连接成一条可以验证的 Agent 工程链路。

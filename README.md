# LimitUpLab

基于真实 A 股收盘数据的复盘 Agent（主要针对近期发生涨停的股票）。

在线访问：[LimitUpLab · limituplab.tech](https://limituplab.tech/)

LimitUpLab 面向收盘后的短线研究场景：支持查询个股行情，最近新闻，热门股票，大盘走势，龙虎榜等。同时针对近期发生涨停的股票，提供首板质量评分，涨停后回撤等策略。

> 本项目用于数据研究、Agent 工程实践和求职展示，不构成任何投资建议。系统不提供买入、卖出、仓位、目标价或收益承诺。



## 项目背景

当前市场上缺乏比较专业、好用的复盘Agent工具，比较主流的如同花顺问财，通达信智能体等，个人使用体验一般（主观）。同时股票的特征（许多结构化数据）也很适合使用Agent来进行分析、归纳、总结，且目前具有同花顺金融API，akshare等开源数据源。因此，做一个股票分析Agent项目既能够学些Agent开发中常见的技术栈（Langchain，Langgraph，工具调用，上下文管理，Agent评测等），也有机会给股民提供一个高效的A股复盘工具，最终上线成为一个成功的产品。

一句话总结，这个项目的核心不是让 LLM 直接预测股票，而是让 LLM 在确定性数据和工具之上完成规划、解释、追问、批判和复盘。



## 项目状态

当前代码以 **LangChain 原生工具调用 + LangGraph 持久化 ReAct** 为聊天主链路：原生 PostgreSQL Checkpoint 保存执行状态，业务事务提交正式答案，SSE 提供进度与重连。Runtime、短期和显式长期 Memory 的设计、迁移和验收边界见 [重构记录](docs/Memory_Runtime_Refactoring.md)；分支改动不表示已发布到生产。

当前阶段为 **V1.5：轻量 LangGraph Agent 与工具输出契约完善**。[V1.5 阶段里程碑](docs/V1.5_Milestone.md) 汇总 `v1.5.0` 的工作、验证结果、限制和下一步。

当前 Windows 工作区和远程单实例生产环境均已正式切换至 PostgreSQL。本地旧 SQLite 运行文件已清理，迁移前一致性归档与迁移工具保留；生产原 SQLite 文件未在本次操作中删除。清理与验证见 [SQLite 退役验收](docs/SQLite_Retirement.md)。生产版本为 `v1.6.3`，ST/北交所研究范围修复、当日收盘补跑与实际接口/Agent 验收见 [v1.6.3 发布记录](deploy/incidents/2026-10-20-v1.6.3-relay-refresh.md)；首次迁移、重启持久化及备份恢复记录见 [生产切换验收](deploy/incidents/2026-10-19-postgresql-cutover.md) 和 [本地切换验收](docs/Local_PostgreSQL_Cutover.md)。当前代码要求显式配置 `LIMITUPLAB_DATABASE_URL`，SQLite 仅保留历史归档与迁移读取；本次清理不代表已发布到生产。操作方式见 [本地手册](deploy/local-postgresql.md) 与 [生产部署恢复手册](deploy/postgresql.md)；多实例部署尚未验收。

离线研究、维护备份和 PostgreSQL 路径已补齐，通过禁止 SQLite 连接的本地验证，并随 `v1.6.2` 发布，见 [SQLite 依赖清理验收](docs/PostgreSQL_Independence.md)。

当前版本已经具备可本地运行和单机部署的完整 MVP：

- 真实涨停、炸板、K 线、板块、人气和龙虎榜数据流水线
- 首板候选池过滤、结构化 Facts、规则评分和置信度
- LangChain 原生工具调用、LangGraph 有界 ReAct、证据表与可恢复 SSE 流式回答
- 可恢复的多会话对话、滚动 Session Memory 和受控上下文
- Explanation、Critic、Review、Evaluation 等轻量 Agent 角色
- 每日 Top10 预测快照、D+1 至 D+5 走势追踪、1进2全市场对照和不可变每日复盘快照
- 来源感知的预测质量审计、确定性基线和多目标评分 v3
- 每日连板晋级率、首板到二板率和各板高梯队统计
- 数据健康、Agent 运行轨迹和缓存
- 盘前推荐页并列提供“一进二接力”“缩量整理”“高位回撤”三个入口，后两者支持固定条件筛选、历史日期查看和缺失原因解释，并独立于一进二前向统计，见 [第一版说明](docs/Consolidation_Strategy_V1.md)

当前代码基线：

| 项目 | 状态 |
| --- | --- |
| 后端自动化测试 | 统一验收入口生成当次测试数量、结果和 JUnit 报告 |
| 本地数据健康检查 | 已实现 |
| LLM 流式问答 | 已实现 |
| Agent 限流与成本审计 | 单访客/IP/全局限制、真实 token 账本已实现 |
| 评分 v3 工程实现 | 已完成，影子验证中 |
| 单机生产部署基线 | Docker、Nginx、HTTPS、持久化卷、备份和日更任务已部署 |

评分结果目前仍处于研究和验证阶段。项目已经形成完整的数据与评价闭环，但尚未证明可以稳定预测次日表现。



## 核心能力

整个项目可以看做由两部分组成，其中一部分为Tool-Using Chat Agent，另一部分为股票新闻、个股行情等看板。这两部分内容相互支撑，相互印证。

### Tool-Using Chat Agent

模型层由 LangChain `ChatOpenAI.bind_tools` 承接，LangGraph 自定义 `StateGraph` 在同一个有界循环中完成思考、行动、观察和最终回答。业务参数、工具权限、结果完整性、确定性计算与最终状态由后端控制。当前生产 ReAct 只支持 LangChain 消息工具调用后端；Requests Provider 保留给独立文本调用，不是聊天运行时回退。实现边界见 [LangChain + LangGraph 集成](docs/LangChain_Integration.md)。

正常问答主链路是：

```text
用户问题
  -> Durable Run 创建任务、绑定 owner/session/message_id
  -> 轻量输入审查（检查系统边界绕过和直接交易指令，拒绝时不查业务工具）
  -> LangGraph（input_review -> agent <-> tool）选择原生工具调用，同步保存 Checkpoint
  -> BusinessToolGateway 校验 profile、Schema、日期回显和参数边界
  -> 工具依次执行，持久化完整回执；模型读取保留全部行与字段定义的无损编码
  -> 模型基于已取得事实直接回答；达到输出长度上限时限一次精简重写
  -> 疑似越界输出的条件语义复核（可配置全量复核）；RunDelivery 事务提交后通过 completed 交付正式答案
```

当前主要工具：

| 工具 | 用途 |
| --- | --- |
| `trading_calendar` | 以交易日历返回最新已收盘交易日、上一/下一交易日和今日交易状态 |
| `market_summary` | 查询最新本地交易日市场环境，并明确开板率与未回封率的分母 |
| `market_index_trend` | 查询上证、深成指和创业板指近 2–20 个交易日走势 |
| `dragon_tiger_list` | 查询同花顺龙虎榜、机构与游资净买额 |
| `first_board_ratings` | 查询首板评级、拆解和风险 |
| `first_board_filter` | 按行业、题材、评级和置信度筛选 |
| `market_event_pool` | 统一查询完整交易日的涨停、跌停和炸板名单或数量 |
| `limit_up_events` | 查询首板、连板、炸板及题材相关涨停事件 |
| `daily_board_promotion` | 统计近 5 日总晋级率、首板到二板率、连板梯队及成功股票明细 |
| `stock_kline` | 查询个股近期 K 线和衍生指标 |
| `stock_financials` | 查询个股利润表、资产负债表、现金流量表，区分年初至今累计与单季推算口径 |
| `stock_profile` | 查询个股公司资料、主营业务、行业、上市信息和当前市值/股本快照；市值按需请求 |
| `index_search` | 将指数名称或代码解析为 thscode，支持宽基、行业、概念、地域和特色指数 |
| `index_quote` | 查询任意指数的当前快照和最近 N 个交易日走势 |
| `index_constituents` | 查询任意指数的成分股名单；来源不含权重、行业和市值 |
| `stock_news` | 查询指定股票近 1 至 30 天的结构化个股资讯，保留来源、时间和链接 |
| `stock_activity` | 汇总指定股票的收盘走势、近期涨停记录、评分补充事实和个股资讯 |
| `review_high_score_picks` | 追踪每日评分 Top10 后续走势，并对比同期全部首板的1进2成功率 |

生产环境依赖上一步名单的查询由模型在观察结果后发起下一轮工具调用；筛选、排序和聚合直接基于工具返回的结构化 rows 与字段说明，模型不再维护 evidence ID 或表达式 DSL。

默认 `LIMITUPLAB_AGENT_PROFILE=v1_close_review` 只暴露收盘后与历史研究工具。BusinessToolGateway 对每一次调用重新校验 allowlist、JSON Schema、日期回显、单股票约束和空集合边界，未知或未开放工具即使由模型生成也不会执行。

业务查询由原生 tool call 发起；日期由模型显式写入工具参数，工具返回的 `data_as_of` 必须与请求日期一致。生产执行事实以 ToolMessage、工具返回的 rows/field_definitions 和最终 trace 为准。

名单由模型基于实际返回的 rows 直接输出 Markdown 表格；截断、来源缺失和覆盖率由工具结果显式标记，返回行数不能自动代表全市场完整名单。开放式解释仍需结合原始证据审阅，引用本身不能证明因果关系。



系统在 PostgreSQL 保留原始会话，短期 Memory 由 LangGraph Thread State 管理。每次模型调用统一组合摘要、近期整轮消息、研究目标、页面上下文、约束及已提交股票引用，并按估算 Token 预算裁剪历史。无法确认“第二只”的显示顺序时先澄清；本轮市场事实必须重新查询。

Memory 按会话所有者隔离，采用消息游标和版本防止重复摘要；旧摘要表仅供首次只读导入。工具结果先做无损编码，大结果可从不可变回执按需分页和选择字段，仍超预算则明确返回 partial，完整证据保留。查询条件与已提交来源绑定，不能从旧摘要猜测“上次条件”。用户可以显式保存、修改、查询和删除回答详略、展示格式、有限研究主题偏好，也可以保存带作用域的自由文本偏好（如 `tool_display:*`、`research:*`）；同一 owner 的新会话可使用，当前问题的明确要求优先。长期记忆每次调用重新读取，删除或到期会使旧上下文失效；交付和历史回答清理只校验本轮实际使用过的记忆版本，无关记忆变化不会打断回答或清空历史文本。普通对话可由后台提取候选，但候选必须显式确认才会生效，不会把旧聊天约束直接升级为长期偏好；同身份重复候选会合并为 NOOP，内容变化会生成带目标版本的 UPDATE 候选，明确的遗忘请求会生成 DELETE 候选；确认时原子替换或删除旧记忆，显式写入则会使待确认候选失效。带作用域的偏好作为 global 同 key 设置的例外，只有在工具/主题作用域命中时才覆盖通用设置。长期记忆对用户无感：前端不展示候选、确认、忽略或记忆管理入口，候选仅通过后台 API 供运维流程处理。历史研究档案只在主动保存/查看时使用，并保留日期和证据来源，不替代当前市场数据。当前基础层已支持作用域、自由文本、内容哈希、状态迁移、工具级确定性展示块和待确认候选；pgvector 语义召回仍按独立阶段推进。



### 行情信息看板

从这个系统的可用性上来说，只有Agent chat是远远不够的。配合上个股详情、龙虎榜、最近新闻等看板才能构成一个高效的复盘系统。这个项目中，主要有以下几个信息看板：

1. 近12小时新闻，数据源来自同花顺和东方财富
2. 最新的龙虎榜
3. 近期市场涨停板近期情况
4. 每一天发生涨停的股票情况

除去以上部分，这个项目还做了策略选股的追踪。具体来说，在每天收盘之后，筛选出符合以下三个策略的股票，并进行展示

1. 当天首次涨停的股票，按照涨停强度，筛选出10个较好的股票（同时在第二天开盘前，根据收盘后的情况动态调整）
2. 近7天发生过涨停，近期处于缩量震荡的股票
3. 近7天发生过涨停，然后大幅回撤的股票

与此同时，系统也会重点记录每天首板中top10的股票在后续的走势，并给出走势看板，特征分析等



## 系统架构

```mermaid
flowchart LR
    A[AKShare / Tonghuashun / Eastmoney / CNInfo / Tencent / Sina] --> B[Daily Data Pipeline]
    B --> C[(PostgreSQL)]

    C --> D[Facts Builder]
    D --> E[Rating Engine]
    E --> G[Prediction Snapshots]
    G --> H[Top10 Outcome Tracking]

    U[User] --> I[React Agent Workspace]
    I --> J[FastAPI Chat / SSE]
    J --> S[(Sessions + Run Registry + Events)]
    J --> K[LangGraph AgentRunner]
    K --> CP[(Native PostgreSQL Checkpoints)]
    K --> L[BusinessToolGateway]
    L --> M[Tool Registry]
    M --> C
    M --> N[Sector / News]
    M --> O[Critic / Review / Evaluation]
    M --> P[Direct Answer + Compliance]
    P --> J
    S -->|Persisted completed| I

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
    participant Runs as Run Registry / Events / Delivery
    participant Graph as LangGraph AgentRunner
    participant Gateway as BusinessToolGateway
    participant Tools as Tool Registry

    User->>UI: 输入自然语言问题
    UI->>API: POST /api/agents/chat/stream
    API->>Runs: 创建幂等 run，保存用户消息
    API-->>UI: accepted + run_id
    API->>Graph: 消息、记忆、profile、截止时间
    Graph->>Gateway: 原生 tool calls
    Gateway->>Gateway: Schema、时态、权限和预算校验
    Gateway->>Tools: 逐调用执行并保存不可变回执
    Tools-->>Graph: ToolMessage 无损编码、来源与完整字段定义
    Graph->>Graph: sync checkpoint；中断后恢复未完成步骤
    Graph->>Graph: 继续查询或直接回答
    loop 查询、生成与校验期间
        Graph-->>API: progress 阶段信息
        API->>Runs: 独立保存进度事件
        API-->>UI: SSE progress
    end
    Graph->>Graph: 输出长度检查与条件合规复核
    opt 输出达到长度上限
        Graph->>Graph: 同证据精简重写一次
    end
    Graph-->>API: 正式响应
    API->>Runs: RunDelivery 原子保存最终回答
    Runs-->>API: 已提交响应
    API-->>UI: SSE completed；一次交付正文、表格与元数据
    UI->>UI: 渐进展示正式答案，表格按完整行展开
```

## 技术栈

### Backend

- Python 3.13
- FastAPI + Uvicorn
- Pydantic
- PostgreSQL 18 + SQLAlchemy / psycopg 3；历史 SQLite 归档使用独立迁移工具读取
- AKShare
- Requests + BeautifulSoup
- LangChain Core + LangChain OpenAI（消息、原生工具绑定和模型适配）
- LangGraph（自定义 StateGraph ReAct 循环）
- DeepSeek 兼容 Chat Completions API

### Frontend

- React 19
- TypeScript
- Vite 6
- React Router
- React Markdown + GFM
- Lucide React

### Agent Engineering

- Native Tool-Calling ReAct
- LangGraph StateGraph
- Tool Schema + Temporal Policy Gateway
- Structured References + Tool Output Contracts
- Durable SSE Task / Reconnect / Cancel
- Controlled Conversation Context
- Persistent Chat Sessions
- Agent Run Observability
- Champion/Challenger Policy Governance

## 项目结构

```text
LimitUpLab/
├── backend/
│   ├── app/
│   │   ├── agents/          # 轻量 LangGraph runner、工具契约、评分与 Review
│   │   ├── collectors/      # 涨停、K 线、板块、指数和扩展数据采集
│   │   ├── repositories/    # PostgreSQL Repository
│   │   ├── routers/         # FastAPI 路由
│   │   └── services/        # 评分、检索、回测、Evaluation 和健康检查
│   ├── scripts/             # 数据同步、回填和启动脚本
│   └── tests/               # 单元测试
├── frontend/
│   └── src/                 # React 工作台、独立 Agent 会话面板、API 类型和样式
├── scripts/                 # 项目级本地启动脚本
└── docs/
    ├── V1.5_Milestone.md    # 当前阶段工作、验证、限制与下一步
    ├── V1.4_Milestone.md    # V1.4 历史发布边界与验证记录
    ├── LangChain_Integration.md # 当前 ReAct 架构说明
    └── code-quality-audit.md # 阶段性代码质量审查
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
```

真实 API Key 不应提交到 Git。未配置 LLM 时，结构化评分、数据检索等确定性接口仍可运行；生产聊天 ReAct 会明确报配置错误，不会伪装成正常回答。

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

### 3. PostgreSQL 数据库配置

业务、研究、维护与测试统一使用 PostgreSQL。启动必须配置数据库 URL；缺失配置直接失败。Schema 由 Alembic 管理，应用只检查版本，不在请求中建表。历史 SQLite 归档与导入工具独立保留。

```dotenv
LIMITUPLAB_DATABASE_URL=postgresql+psycopg://limituplab:REPLACE_WITH_PASSWORD@127.0.0.1:55433/limituplab
LIMITUPLAB_DB_POOL_SIZE=5
LIMITUPLAB_DB_MAX_OVERFLOW=5
LIMITUPLAB_DATA_DIRECTORY=backend/data
```

本地初始化与服务启动见 [本地手册](deploy/local-postgresql.md)。回归使用独立的 `LIMITUPLAB_TEST_DATABASE_URL`，禁止指向业务库。连接池与事务锁保护现有单实例契约，多实例吞吐仍需独立验收。

如需本地代理：

```dotenv
LIMITUPLAB_PROXY_URL=http://127.0.0.1:17891
```

### 4. 配置同花顺结构化数据

项目通过官方 `hithink-finance` CLI 获取未复权个股日 K、最新行情、热股榜、龙虎榜、交易日历和远端涨停池。凭据保存在 CLI 系统凭据库，不写入项目 `.env`：

```powershell
npm.cmd install -g @hithink-tech/hithink-finance-cli
hithink-finance auth login
hithink-finance symbol search --q 600519 --limit 1 --format json
```

业务读取仍以项目所选数据库中的快照为先；缺少日线或当日收盘时统一经过 MarketDataProvider 调用 CLI，固定使用 `adjust=none`，并把 CLI 的“股”转换为项目统一的“手”。CLI 不可用或请求失败时，日线和收盘快照显式回退腾讯行情并保留真实来源；分钟 K 继续使用新浪、东方财富。V1 Agent 不暴露同花顺实时工具；这些工具仅在 `extended` 研发配置中用于 V2 开发，请求失败时会明确返回错误，不会伪造结果。

项目的同花顺 CLI 调用固定使用 `--source remote`，不读取或创建本地 DuckDB。为兼容 CLI 0.1.5 在选源前打开默认库的问题，每次请求以 `--db` 指向独占临时目录下未创建的文件路径，覆盖 CLI 默认路径及 `HITHINK_FINANCE_DB_PATH`；请求完成或失败后清理临时目录。如果 CLI 创建本地文件或返回 `meta.source=local`，采集器明确报错。此兼容处理保留 CLI 的凭据管理和远端重试机制，无须初始化 DuckDB，也不改动已有个人数据库。修改后需重启已运行的后端或更新进程，新启动的任务才会加载修复。

同一 PostgreSQL 业务库的后端与任务共用同花顺调用节流，默认两次 CLI 启动至少相隔 1 秒，可通过 `LIMITUPLAB_HITHINK_MIN_INTERVAL_SECONDS` 调整。429 会触发共享冷却和有限退避，一次采集最多启动 3 次 CLI，排队、退避及执行共同受 `LIMITUPLAB_HITHINK_TIMEOUT_SECONDS` 预算限制；4001 已由 CLI 重试，项目只共享冷却，不叠加重试。CLI 自身的分页/重试仍由 CLI 管理，此设置不等于上游公布的 HTTP 配额。错误信封从 stdout/stderr 结构化解析，保留错误码与 request ID；不凭错误文本猜测可重试状态。

盘前页的 partial 草稿在 5 分钟冷却后允许补刷新，后台最多追加 5/10/20 分钟三轮恢复，随后恢复 08:00 调度。完整草稿、已固化结果和 09:30 截止边界保持原规则；采集跨过截止时间会丢弃本轮新证据。新闻与财报失败分别统计，并明确哪些股票沿用了缓存、哪些没有可用缓存；页面只读保存的快照，刷新网页不会直接重跑采集。

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
4. 对最新完整交易日生成一进二初始基线，历史补算继续标记为 `historical_backtest`。
5. 回填近期 Top10 的 D+1 至 D+5 走势。
6. 输出 JSON 数据健康报告。

一进二的候选、评分特征及市场/行业上下文统一排除 ST（含 `*ST`）、退市风险警示和北交所股票（`4`、`8`、`920` 开头）。原始行情仍保留这些股票，但它们与同花顺名单不匹配的东方财富补充记录，不再阻塞一进二更新。日更报告分别记录全市场采集状态与 `relay_inputs_complete`，被排除的缺口列入 `relay_excluded_gaps`；普通股票缺少必要字段或整个接口请求失败时仍阻止发布。历史已固化预测不重新计算。

盘前推荐只保留一进二接力链路，以当日全部合格首板为候选池并动态展示 Top10。流水线在
15:30 收盘数据就绪后生成一次草稿，下一交易日 08:00 再批量更新最新行情、个股新闻、龙虎榜和当前可用人气榜，并读取同花顺最新季度利润表；
财报缓存 24 小时，避免对不会频繁变化的数据重复请求。收盘基础分保持可
追溯，盘后新增公告、新上龙虎榜和可比较的人气变化以有边界的修正更新研究
分与名次；动态一进二 Top10 允许换入换出，盘前推荐与首板页前 10 名读取同
一份当前版本。系统只保存当前草稿和字段变化记录；目标
交易日 08:00 完成最后一次刷新，随后将 Top10 固化为不可变最终预测，
并以最终一进二 Top10 替换复盘基线。若服务未能在 09:30 开盘前完成固化，系统会
记录 `missed_cutoff`，停止更新排名，且不会使用开盘后行情、新闻或人气数据补算：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_recommendation_refresh_loop.py --once
```

本地一键启动和 Docker Compose 会自动启动每日固化进程。进程启动时先检查当前候选状态，随后按每日 08:00 调度；08:00 至 09:30 间可以补做当日固化，09:30 后只记录错过窗口，不采集开盘后证据补算。单独启动 Uvicorn 和 Vite 不包含该后台进程；本地可运行 `python scripts/start_dev_detached.py --skip-data-check`，复用已运行的前后端并补启推荐刷新进程。

候选目标日通过实际 A 股交易日历确定，跨周末和节假日均取基准日之后的首个交易日。日历缺失或查询失败时，目标日保持未确认并展示数据缺口，不按工作日猜测。页面同时展示候选基准日、目标日和完整快照时间；旧候选标记为历史快照。最新刷新为空时，保留历史名单供参考，并单独说明最新目标及未固化状态，不把旧名单改成新目标日。

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

任务默认在每个工作日 `15:30` 执行，并启用错过后补跑、失败重试和禁止并发执行。一次执行会自动完成交易日判断、原始数据同步、首板特征与 enrichment、收盘 Top10 固化、盘前研究候选更新、历史 Top10 的 D+1 至 D+5 K 线缓存、Outcome 回填、每日复盘快照和健康检查。当日收盘预测快照就绪后，任务会立即生成下一交易日的一进二草稿；独立进程在下一交易日 08:00 做唯一一次盘前更新并完成固化。

预测来源有严格时间口径：同日 15:30 收盘后保存收盘基线并生成草稿，下一交易日 08:00 刷新并固化最终 Top10。公共写入层校验阶段、实际生成时间、信息截止和交易日历；直接脚本补算过去日期只能写入 `historical_backtest`。终选不会被普通刷新覆盖，处理跨过 09:30 开盘则回滚，错过窗口后进入 `missed_cutoff`。历史异常使用独立审计标记排除，保留原件；复盘页面可按阶段与评分版本分别查看。

Agent 将“近期涨停后回撤比较多的股票有哪些”等表述固定解释为盘前推荐的高位回撤名单查询，直接使用同一套七交易日事件窗口与确定性形态规则返回完整名单；“比较多”只描述回撤幅度，不会触发历史形态比较。

一进二动态研究排序使用明确的信息时点契约：原始规则分与截至基准交易日 `15:00` 已公开的新闻、财报共同构成收盘综合分；基础首板评分中已存在的龙虎榜和人气快照不会重复计分。动态修正只接受该时点后的新增公告、新上龙虎榜，以及相对基础快照可验证的人气名次变化；缺少人气基线时只展示最新排名并写入缺失项，不猜测涨跌。盘后各项修正合计限制在 `±6` 分，每只候选保留分项修正、动态合计和证据原因，便于回放。

手动执行、查看任务状态和卸载任务：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_daily_close_loop.py --trigger manual
cd ..
powershell -ExecutionPolicy Bypass -File .\scripts\daily_close_loop_task.ps1 -Mode Status
powershell -ExecutionPolicy Bypass -File .\scripts\daily_close_loop_task.ps1 -Mode Uninstall
```

每次执行都会向所选数据库写入审计记录。最新报告保存在 `backend/data/daily_close_loop_latest.json`，部分完成或失败时额外生成 `backend/data/daily_close_loop_alert.json`；前端系统状态条和 `GET /api/agents/daily-pipeline-status` 可查看最近运行结果。

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
| `GET` | `/api/analysis/daily-promotion-report` | 晋级率、最新事件日期及交易日历/收盘数据缺口 |
| `GET` | `/api/stocks/{symbol}/kline` | 个股日 K 线 |
| `GET` | `/api/stocks/{symbol}/market-data` | 个股详情聚合行情、最新收盘和位置判断 |
| `GET` | `/api/stocks/{symbol}/intraday-history` | 最近 2-10 个交易日的分钟分时，默认五日一分钟周期；本地分钟线仅保留最近 7 个交易日 |

完整接口定义以 Swagger 为准。

每日连板晋级率和高分票 1 进 2 对照均按交易日历确认相邻交易日，正常跨长假可以计算；若中间交易日的收盘事件缺失，则不跨过该日推算。日历不可用时明确提示缺口，不按自然日间隔或工作日猜测。复盘页同时展示最新事件日期和可计算统计日期；旧版复盘快照保留原件，读取时按新版口径重新计算响应，不覆盖历史预测或复盘记录。

## 测试

推荐从项目根目录运行统一离线验收。需要 Python 3.13、Node.js 24，并先安装依赖：

```powershell
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
backend/.venv/Scripts/python.exe scripts/check_project.py
```

Linux 使用对应虚拟环境的 `python scripts/check_project.py`。也可通过 `--scope backend` 或 `--scope frontend` 单独验收一侧。

该入口依次运行完整 pytest、全部前端逻辑测试以及 TypeScript/Vite 生产构建。每次使用独立数据库和测试目录，关闭真实 LLM，并在 `output/validation/<运行标识>/` 保存各步骤日志、JUnit 和 `summary.json`。任一步失败都会使整体退出码非零，但其余独立检查仍会执行。它不替代浏览器端业务验收、部署检查或压力测试。

聊天评测框架已删除；离线回归不代表真实模型回答质量或稳定性已验收。预测结果复盘使用的 Evaluation Agent 及其 HTTP 接口仍保留，两者用途不同；该能力不再作为 Agent 工具暴露。

GitHub Actions 配置在 `.github/workflows/validate.yml`，对 PR、main、codex 分支及 `v*` 标签推送运行 Windows/Linux 两套检查，使用相同验收入口，不需要行情或模型密钥。标签检查通过后触发生产部署，流程见 [标签自动部署说明](deploy/Tag_Deployment.md)。失败日志保留 7 天；测试数据库不上传，分支保护需在仓库设置中配置。

ReAct 执行与证据契约见 [LangChain + LangGraph 集成](docs/LangChain_Integration.md) 和 [代码阅读指南](docs/code-reading-guide.md)。以下单项命令仍可用于定位失败。

运行后端测试：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

前端生产构建：

```powershell
cd frontend
npm.cmd run build
```

## 数据源与边界

当前数据来自官方同花顺 CLI、AKShare 封装或公开接口：

- 同花顺个股日 K、最新行情、热股榜、龙虎榜和远端涨停池
- 东方财富涨停池和炸板池
- 同花顺与东方财富行业板块行情
- 腾讯日线与收盘行情回退源，Sina、东方财富分钟 K 线
- CNInfo 上市日期
- 东方财富龙虎榜和人气快照回退源
- 360 搜索、Bing 和 DuckDuckGo 的公开搜索结果回退链路

需要注意：

- 项目定位是收盘后复盘，不是实时交易终端。
- 免费公开数据源可能限流、延迟、变更字段或暂时不可用。
- 历史人气和龙虎榜时点数据无法在所有日期完整重建。
- 日 K 统一使用未复权价格；同花顺历史行情成交量由“股”标准化为“手”，来源和成交额随行持久化。
- Web 搜索内容被视为外部不可信证据，只用于解释增强，不直接决定评分。
- 生产采用 PostgreSQL 单实例应用部署；当前代码已移除 SQLite 运行兼容，多 API 副本与并发吞吐需要独立验收。

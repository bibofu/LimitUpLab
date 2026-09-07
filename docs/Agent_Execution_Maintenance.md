# Agent 工具执行层维护

`chat.py` 负责对话上下文、规划、回答和降级。工具执行交给 `app/agents/tool_execution`：

| 模块 | 职责 |
| --- | --- |
| `__init__.py` | 显式 handler 注册、逐次 profile 检查、按计划顺序执行、汇总证据 |
| `context.py` | 单次请求的可变状态，包括评级缓存、Facts、trace 和来源 |
| `market.py` | 市场事件、指数、晋级率、板块、人气及扩展涨停池 |
| `stocks.py` | 个股行情、资讯、龙虎榜及扩展搜索 |
| `ratings.py` | 首板评级、候选过滤及 Critic |
| `review.py` | 预测质量、回测、评价、Top10 追踪及策略状态 |
| `helpers.py` | 无对话编排依赖的参数解析与证据序列化 |

执行模块不能反向导入 `chat.py`。每次 `execute_tool_calls` 创建自己的 `ExecutionState`，handler 注册表只保存函数，不保存请求数据。顺序执行是有意保留的：同一计划中的候选过滤可以复用前一步评级，但不能复用另一请求的评级。

新增工具时，在对应领域模块增加 handler，并显式注册到 `HANDLERS`；继续同步 `tools.py` 的工具 Schema、Capability Contract、Tool Policy、失败处理和测试。不要在调度入口新增按名称判断的业务分支。工具是否允许执行仍由 profile 控制，注册并不等于授权。

`test_tool_execution.py` 检查公开 Schema 与执行注册的一致性、越权及未知工具拒绝、请求状态隔离、评级复用和工具失败后的后续查询。原有对话、参数契约、profile 和离线评测继续验证对外行为。

本次拆分保留原有错误处理边界、Facts 键名、trace、日期口径和返回模型。同时修复了扩展涨停池的完整名单分支缺少解析函数导入的问题；该修复不开放 V1 原本禁用的工具。

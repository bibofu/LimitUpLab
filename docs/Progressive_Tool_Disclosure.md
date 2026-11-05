# 渐进式工具披露

## 目标

一次 Agent run 不把全部 25 个业务工具定义发给模型，而是：

1. 用确定性 profile 给出初始工具集；
2. 暴露一个 `search_tools` 元工具；
3. 模型缺少能力时搜索工具，匹配项在下一轮加入；
4. 用独立预算约束工具发现，不让搜索挤占数据查询次数。

这参考 LangChain Deep Agents 的 progressive tool disclosure：全量工具注册在运行时，初始只暴露少量工具，模型通过 `search_tools` 按需发现，后续模型调用再加载匹配 schema。

## 模式

环境变量 `LIMITUPLAB_PROGRESSIVE_TOOLS`：

| 值 | 初始业务工具 | 搜索 | 说明 |
| --- | --- | --- | --- |
| `off`（默认） | 请求 profile | 不可用 | 保持原有确定性行为 |
| `hybrid` | 请求 profile | 可用 | 确定性命中 + 长尾按需发现 |
| `full` | 空 | 可用 | 实验模式，只从 `search_tools` 和 `read_tool_result` 开始 |

`LIMITUPLAB_PROGRESSIVE_MAX_TOOLS` 控制活动业务工具上限，默认 20，范围 1–25。

## 流程

```text
resolve_tool_profile
  -> initial business tools
  -> + search_tools + read_tool_result
  -> model round
       -> search_tools("财务")
       -> search result rows
  -> tools() 把未激活的匹配项加入 discovered_tools
  -> 下一轮 definitions = initial + discovered + meta tools
```

- 初始业务工具保持分类目录顺序，已发现的工具按发现顺序追加，尽量保留稳定的 prompt 前缀。
- `search_tools` 只返回工具名、简短说明、时间模式以及是否已可见；完整 schema 在下一轮才发送。
- `read_tool_result` 始终可用，继续用于分页读取已持久化结果。
- 未激活工具不能在 gateway 中执行；模型必须通过搜索发现。

## 搜索

实现：`backend/app/agents/progressive_tools.py`。

- 在工具名、短描述、notes 上做关键词评分；
- 中文按整词、分词和二字片段匹配；
- 常见能力词有别名表，如“成交额/K线”指向 `stock_kline`，“财务/营收”指向 `stock_financials`，“公司/介绍”指向 `stock_profile`；
- 查询可以解析为公司名或股票代码时，优先返回 `stock_profile`、`stock_kline`、`stock_financials`、`stock_news`、`stock_activity`；
- 单次最多返回 8 个，默认 4 个；
- 已达活动上限时，不再加入新工具，并在 observation 中说明原因。

## 预算

`backend/app/agents/runner/guardrails.py`：

- `MAX_MODEL_ROUNDS=6`
- `MAX_TOOL_CALLS=8`：数据查询工具
- `MAX_META_CALLS=8`：`search_tools` 和 `read_tool_result`
- `MAX_SEARCH_CALLS=3`
- `DEADLINE_SECONDS=90`

搜索和读取回执是元操作，不占用 8 次数据查询额度；但仍受模型轮次、元操作额度和总时限约束。

## 观测

- `RunnerState.discovered_tools` 随检查点持久化，恢复后继续生效；
- LLM usage context 记录 `progressive_mode`、`discovered_tool_count`、`tool_count`、`tool_definition_chars`；
- 工具 trace 保留 `search_tools` 的 query、返回行和 `already_visible`。

## 验证建议

对同一组真实问题分别运行：

1. `off`：确定性 profile 基线；
2. `hybrid`：推荐候选；
3. `full`：全量渐进披露实验；
4. 全量 25 工具直接暴露（仅在评测脚本中）。

比较：

- profile / 初始工具命中率；
- `search_tools` 调用次数和额外模型轮次；
- 每轮工具定义字符数；
- 首 token 延迟与总耗时；
- 工具选择正确率、无效工具调用率；
- 最终回答的完整性和 `partial` 比例。

## 基准结果

2026-11-05 本地 `deepseek-v4-flash`，12 个场景 × 4 种模式，最多 4 个选择轮次。业务工具结果使用固定 synthetic observation，只衡量工具选择与上下文成本；真实工具执行另做端到端验证。

**工具 schema 单独的 prompt token 成本**（同一短消息，`tool_choice=auto`）：

| 工具集 | 工具数 | 定义字符 | 实际 prompt token | 相对无工具增量 |
|---|---:|---:|---:|---:|
| 无工具 | 0 | 2 | 13 | 0 |
| stock profile | 6 | 9,936 | 3,283 | +3,270 |
| review profile | 4 | 7,379 | 2,503 | +2,490 |
| 全量 | 25 | 34,196 | 10,753 | +10,740 |

**完整 run（12 场景平均）**：

| 模式 | 工具命中率 | 平均模型调用 | 平均 prompt token | 相对全量节省 |
|---|---:|---:|---:|---:|
| deterministic | 83.3% | 2.50 | 11,728 | 61.0% |
| hybrid | 100% | 3.00 | 15,055 | 50.0% |
| full | 83.3% | 3.75 | 15,840 | 47.3% |
| 全量 25 工具 | 100% | 2.50 | 30,081 | 基线 |

结论：

- `hybrid` 与全量工具**命中率相同**，prompt token 约少一半；
- 代价是平均多 0.5 次模型调用（发现工具）；
- `deterministic` 更省 token，但漏掉“复盘 + 个股资料/财务”这类长尾组合；
- `full` 起步过小，平均多 1.25 次调用，且本轮出现一次超时，不建议作为默认。

## 当前边界

- 搜索是关键词评分，不是向量检索或小模型路由；高歧义问题仍可能匹配不到。
- `hybrid` 的初始 profile 仍是关键词规则，股票名称未命中时会先落到 `general`。
- 工具定义会随发现增长；活动上限防止重新变回全量，但复杂问题仍可能接近上限。
- 默认 `off`，需要显式设置环境变量后启用。

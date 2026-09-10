# Agent Eval V2 Dataset Audit Report

## Audit scope and positioning

This audit keeps `agent_chat_eval_dev_v2.json` as an **offline deterministic
contract/regression dataset**. It evaluates Query Understanding, capability
routing, tool selection and parameters, Tool Policy, frozen execution outcomes,
Grounding, failure disclosure and safety. It is not presented as a complex
reasoning, ReAct, replanning or full Agent-intelligence benchmark.

The review covered the planner/query parsing path, capability and Query contracts,
Tool Policy, tool registry schemas, Grounding and answer validation, the V2 runner,
the frozen tool fixture, the V1 migration manifest and Agent Eval documentation.

## Dataset changes

| Item | Before | After |
|---|---:|---:|
| Total Dev cases | 120 | 89 |
| Capability basics | 69 (3/capability) | 46 (2/capability) |
| Multi-turn | 18 | 12 |
| Composite | 12 | 10 |
| Failure | 9 | 9 |
| Safety | 6 | 6 |
| Out-of-scope / clarification | 6 | 6 |

Thirty-one low-value cases were removed: 23 third paraphrases from capability
basics, 6 placeholder-heavy multi-turn cases and 2 redundant composites. No new
questions were generated merely to preserve the old total. Existing case IDs were
kept where possible, so the remaining IDs intentionally contain gaps and V1
migration lineage stays auditable.

## Consistency fixes

The audit confirmed and repaired 13 semantic inconsistencies across the old
question/expected/fixture triples:

- Three explicit-symbol mismatches (`300750`, `600519` or `001299` questions
  bound to another stock's evidence).
- Two stock-name mismatches where 贵州茅台 questions used 宁德时代 facts.
- Two date mismatches, including the previous-trading-day case whose evidence
  still pointed at 2026-05-15.
- Two event-type mismatches where limit-down and broken-board questions used a
  `limit_up` evidence record.
- Three shape/board-height mismatches (三连板, 横盘缩量 and 断板修复 bound to a
  first-board/high-drawdown record).
- One composite cross-tool entity mismatch where a 龙虎榜 stock and the K-line
  stock were unrelated.

The frozen market world now has one explicit symbol/name/sector map for 思泉新材
`301489`, 宁德时代 `300750`, 美能能源 `001299`, 贵州茅台 `600519`, 浦发银行
`600000` and 中国卫通 `601698`. Stock-like placeholders such as “样本人气股”、
“样本科技”、“样本龙虎榜股”和“样本路径股” were removed. Analytical cohort
labels remain descriptive (for example “高分Top10组合”) rather than pretending to
be securities.

## Failure and Tool Policy changes

Eight failure prompts that explicitly told the Agent about an outage or missing
result were rewritten as normal business questions. All nine failures are now
injected by `agent_chat_eval_tool_fixture_v2.json` `case_overrides`:

- `CEV2-D100`: `stock_news -> error`
- `CEV2-D101`: `finance_news -> error`
- `CEV2-D102`: `stock_kline -> error`
- `CEV2-D103`: `dragon_tiger_list -> empty`
- `CEV2-D104`: `sector_performance -> partial`
- `CEV2-D105`: `first_board_ratings -> empty`
- `CEV2-D106`: `post_limit_screen -> partial`
- `CEV2-D107`: `review_high_score_picks -> empty`
- `CEV2-D108`: `scoring_policy_status -> error`

Their expected behavior is explicit missing-data disclosure, not a generic refusal.
The runner/schema now supports `optional_tools` with backward-compatible defaults.
Policy allows useful optional execution without counting it as a missing required
path or harmful repair. `forbidden_tools` is no longer the complement of
`required_tools`; it is normally empty and is used only for a concrete wrong-domain
substitution, such as broad finance news replacing stock-specific news.

## Multi-turn improvements

All 12 retained multi-turn histories now contain frozen facts rather than “已返回”
placeholders. Their referenced entities, dates, sets and rankings exist in the tool
fixture. Coverage includes 2 previous-date, 6 previous-result-set, 2 previous-entity
and 2 previous-first-entity scenarios. Six low-value multi-turn cases were removed.

## Automated self-check

`test_agent_eval_dataset_consistency.py` now verifies:

- unique IDs, legal primary types/tags and valid Pydantic schema;
- registry existence and disjoint required/optional/forbidden tool sets;
- required tool fixture availability and tool argument schema compatibility;
- global symbol/name consistency and exact evidence `source_path` resolution;
- Query trade date linkage to at least one relevant evidence claim;
- fixture-injected failure state and ordinary user wording;
- real multi-turn context support; and
- absence of normal required/optional tools in safety and out-of-scope cases.

The audit also found implementation bugs rather than Golden bugs: standalone live
planning omitted deterministic limit-up Query parameters for capability-first
plans, and Query View misclassified broad “涨跌停结构”, Top10 reviews and plain
“有多少” questions. The correct expectations were retained, the implementations
were fixed, and the regressions are recorded as `BC-014` and `BC-015`.

## Known limitations

- A tool has one shared frozen payload for successful cases; it is not yet a full
  per-query simulator that filters every returned row from arguments.
- Stock-name resolution is not a first-class field in Query View, so the dataset
  consistency test enforces name/symbol identity outside that trace contract.
- The frozen world is internally consistent but deliberately small and should not
  be interpreted as a historical reconstruction of the real 2026-05-15 market.
- A single `trade_date` field cannot fully describe two-date promotion semantics;
  tool parameters carry the observation window.
- Optional-tool allowlists remain human-reviewed policy metadata.

These limitations are acceptable for a deterministic contract suite. Behavioral
reasoning quality, replanning after novel observations and model stability belong
in a separate live behavioral dataset with repeated LLM trials and calibrated
judging.

## Verification results

- Dataset consistency and Query Contract checks: passed.
- Agent Eval, dataset loader, runner, gate, Query, Tool Policy, Grounding,
  capability and execution regressions: **127 passed, 16 subtests passed**.
- Final offline deterministic suite: **89/89 cases passed**, including 15/15
  Critical cases, 422/422 Query fields, 94/94 required tool executions, all nine
  injected failure outcomes and 100% evidence completeness.
- No test failures remain. The earlier Windows `tmp_path` ACL setup error was an
  environment issue; the same tests passed when rerun outside the restricted
  temporary-directory sandbox.

Planner raw accuracy, model stability, provider behavior, tokens and LLM Judge
quality remain not applicable to this offline run. A separate live evaluation is
still required before making claims about model performance.

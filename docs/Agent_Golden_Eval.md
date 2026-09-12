# LimitUpLab Chat Eval V2

## Scope

Chat Eval V2 only evaluates the Tool-Using Chat Agent. It does not score Top10,
first-board strategy quality, or D+1 to D+5 investment outcomes. One case follows
the same seven independently diagnosable stages as production:

`Query Understanding → Planner → Tool Policy → Execution → Grounding → Final Answer → Efficiency`

Safety, data boundaries, repeatability and provider failures are cross-cutting
gates. Reports do not manufacture a weighted overall score.

## Dataset and frozen facts

The committed Dev split is
`backend/tests/fixtures/agent_chat_eval_dev_v2.json`; it contains exactly 89
cases: 46 capability basics (two materially different cases for each of 23 V1
capabilities), 12 multi-turn, 10 composite, 9 empty/partial/error, 6 safety and
6 ambiguity/out-of-scope cases.
The private Holdout contains 40 cases and is loaded only from
`LIMITUPLAB_EVAL_HOLDOUT_PATH`. Dev and Holdout duplicates are rejected.
Together the current splits contain 129 cases. Holdout remains private and keeps
one capability case per V1 capability; Dev intentionally no longer pads its size
with low-value paraphrases.

Every case has a timezone-aware `anchor_datetime`. Relative words such as today,
yesterday and previous trading day are resolved from that anchor, never from the
machine clock. Tool output comes from the versioned
`agent_chat_eval_tool_fixture_v2.json`, so offline and live evaluations never query
the current database, network, or market. Expected facts use the relation tuple
`(entity, date, metric, value, source_path)`.

The 89-case Dev artifact has non-empty Query View goldens on all cases,
tool-parameter goldens on every case that requires a tool, and relation evidence for every usable
`ok`/`partial` required tool. The generator is deterministic and checked by the
dataset loader; generated output is still reviewed and committed as the public
Golden artifact.

Tool expectations distinguish `required_tools`, `optional_tools` and
`forbidden_tools`. Optional tools are useful alternative or enrichment paths and
do not become mandatory merely because one author preferred them. Forbidden tools
are listed only when they would substitute the wrong evidence domain or violate a
boundary. Failure observations are injected by fixture `case_overrides`; user
questions remain ordinary business questions.

The retired 50-case V1 artifact was not copied wholesale. The review disposition
for every `G001` through `G050` is recorded in
`backend/tests/fixtures/agent_chat_eval_v1_migration.json`: valid intents were
either migrated exactly or re-authored against the V2 fixture; cases with date,
fixture, scope or relation-evidence conflicts were explicitly rejected. A loader
test requires all 50 IDs, non-empty reasons and valid replacement IDs.

## Evaluators

- Query, Planner, Policy, Execution, Grounding, deterministic answer rules,
  Safety and Efficiency are deterministic.
- Offline Planner is always `not_applicable`; a scripted plan validates the
  surrounding contracts without pretending to measure model ability.
- Live Planner evaluates raw model capabilities. The trace separately preserves
  raw and server-resolved capabilities/tool calls. The Policy stage derives the
  pre-policy tool set only from raw capabilities, records server-added required
  tools as repairs, and separately rejects missing or harmful repairs.
- Grounding binds values to the same entity/date record, distinguishes metric
  categories, and requires expected evidence to be used by the answer.
- LLM Judge only scores relevance, completeness, explanation, uncertainty and
  concision from 0 to 2. It receives the question, expected behavior, frozen tool
  facts and answer, and is explicitly forbidden from supplying market knowledge.
  Passing requires at least 8/10 and no zero dimension.

Judge configuration is intentionally separate. `--judge` requires a pinned
`LIMITUPLAB_EVAL_JUDGE_MODEL` and an API key. Optional
`LIMITUPLAB_EVAL_JUDGE_BASE_URL` selects its endpoint. Missing configuration is a
`configuration_error`; it is never silently skipped. Judge requests use
temperature 0, and Judge tokens are excluded from product answer cost.
Mature release gating also requires a 50-item double-labeled calibration artifact
at `LIMITUPLAB_EVAL_JUDGE_CALIBRATION_PATH`. Every dimension must reach Cohen's
κ ≥ 0.70, human agreement ≥ 80% and Judge-to-human-consensus agreement ≥ 80%.

## Run modes

Offline Dev contract gate:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --dataset dev --mode offline --trials 1 --summary-only
```

Production ReAct behavior now uses the separate Live Behavioral Eval runner:

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_live_eval.py --case-id LIVE-SIMPLE-002 --trials 1
.\.venv\Scripts\python.exe scripts\run_agent_live_eval.py --trials 3 --judge
```

The former `run_agent_eval.py --mode live` Planner/Answer pipeline is retired.
Its old release command must not be used. Public Live cases are not a substitute
for private Holdout acceptance; migration of the full release gate is pending.
Frozen Dev/Holdout artifacts and historical reports remain unchanged.
ReAct capabilities are labeled from raw tool selection (`capability_basis`),
not measured as a separate Planner. Independent Replan and graph compilation
metrics are removed; reports retain decisions, actual calls and task statuses.
Live reports use `output/agent-live-eval/`; replay and shadow use `output/agent-eval/`.

Anonymous online shadow reads already persisted successful Agent runs. It does not
rerun a user answer and does not write the original question or run id into the
report:

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_eval.py --mode online-shadow --sample-size 40 --seed week-1
```

`--case-filter` matches case id, tag or capability. All completed reports are
written to `output/agent-eval/<run_id>/summary.json` and `failures.json`; the latest
completed artifact is also published atomically as `output/agent-eval/latest.json`.
`GET /api/agents/eval` only reads that artifact. System health runs a fixed
12-case offline smoke sample and never launches the full or paid suite.

## Metrics and gates

Reports include stage pass/fail/N/A, Query field errors, capability macro/micro
precision/recall/F1 and per-capability recall, Planner raw accuracy, Policy repair
diagnostics, required/forbidden tools, parameters, result states, claim precision,
evidence completeness, deterministic answer and Judge scores, pass@1, 3/3
stability, provider failures, tokens, tool calls and p50/p95 latency. Breakdowns are
provided by dataset, capability, severity, result state, primary type and turn type.

The mature release gate implements the approved thresholds in code. During the
transition, a full live release requires all Critical cases plus an explicitly
approved completed live baseline from `LIMITUPLAB_EVAL_BASELINE_PATH`; pass@1,
p95 product latency and p95 product tokens may not regress beyond the approved
limits. Set `LIMITUPLAB_EVAL_MATURE_GATE=true` only after Judge calibration and the
full mature thresholds are accepted. Samples and case-filtered runs are marked
informational, never release-passing.

## Maintenance

An online failure enters Dev only after human confirmation. Add the original
anonymized wording, expected contracts, fixture facts and `bad_case_id`, then update
`badCase.md`. Changes to Query Contract, Tool Policy, planner traces or Grounding
require layer-isolation tests and a code-quality audit record. Do not weaken a
Golden expectation merely to make a regression pass.

# Reading the LimitUpLab code

This guide is a starting point for following the English comments in the code.
The project is a research workbench. Scores, filters and historical statistics
come from deterministic code and recorded data; the model plans and explains.

## Start with one user question

1. `frontend/src/components/AgentChatDock.tsx`: sends the question and session
   context, displays progress, and publishes only the completed answer.
2. `frontend/src/utils/agentChatTransport.ts`: parses SSE, reconnects once with
   the event cursor, and preserves request identity for an in-page retry.
3. `backend/app/routers/react_chat.py`: owns authenticated request admission,
   workers, cancellation, reconnect and atomic completion persistence.
4. `backend/app/agents/react_runtime/lifecycle.py`: stores run identity,
   checkpoints, call results and events, with owner isolation and idempotency.
5. `backend/app/agents/chat.py`: the small public ReAct entry. There is no
   separate capability Planner, legacy routing or template-mode switch.
6. `backend/app/agents/react_runtime/runtime.py`: the single compiled StateGraph
   performs model decisions, Policy checks, tool calls, observation and final
   validation. Model/tool/deadline limits bound the same loop.
7. `react_runtime/tools.py` and `tool_schema.py`: validate explicit arguments and
   dispatch to `agents/tools.py`. They do not silently add tools based on wording.
8. `react_runtime/catalog.py`, `contracts.py` and `evidence.py`: define tool
   capabilities, controlled computation, full result references and provenance.
9. `react_runtime/context.py`: separates historical evidence from new facts and
   gives current explicit conditions priority over earlier context.
10. `models.py`: builds user-visible metadata and retains compatibility for
    historical stored traces. `react_chat.py` persists the final response once.

For evaluation, `chat_live_eval_runner.py` executes this production loop with
frozen tools. `chat_eval_runner_v2.py` retains fixture replay, shadow and report
reading; it no longer runs a separate Planner/Answer model pipeline. The deleted
`eval_runner.py`, `tool_policy.py`, `tool_execution/` and old chat Prompt/template
modules are not part of the current call graph.

## Follow the data behind an answer

| Code area | Responsibility | What to look for in the comments |
| --- | --- | --- |
| `collectors/` | Adapt remote providers to local typed records | Source fields, units, timezones, timeouts, unavailable data |
| `repositories/` | Read/write SQLite records | Keys, transactions, ownership, immutable snapshots |
| `services/` | Calculate and combine domain facts | Trading-day windows, denominators, eligibility, missing values |
| `agents/first_board.py` | Filter candidates, build Facts and score | Exclusions versus missing data, policy version, confidence |
| `agents/query_contract.py` | Shared event normalization plus legacy eval/compat parsing | Do not mistake its full parser for the production ReAct router |
| `post_limit_query_contract.py` | Interpret post-limit shape research | Anchor, cutoff, shape thresholds and statistical windows |
| `agents/tools.py` | Expose domain operations to chat | Tool inputs, full output, compact trace, active profile |
| `models.py` | Define API and stored data shapes | Optional fields, validators and uniform result states |
| `backend/scripts/` | Run collection, backfill and evaluation workflows | Step order, restart behavior, reports and failure states |
| `frontend/src/` | Fetch, format and render facts | State updates, stale requests, empty states and source links |
| `backend/tests/`, `frontend/tests/` | Explain expected behavior with controlled cases | Regression scenario, fixtures and assertions |
| `deploy/`, `scripts/` | Start, verify and deploy the system | Process ownership, locks, backups and recovery |

## Terms used in function comments

- **Facts**: structured evidence from tools or deterministic calculations, used
  by the answer writer. Facts are distinct from the conversation summary.
- **Trace**: the inspectable record of native model decisions, Policy allow/reject,
  tool inputs/outcomes, evidence references and the final answer check.
- **Query contract**: a deterministic domain parameter contract used inside
  specific tools or compatibility evaluation. Production chat semantics come
  from the ReAct message/tool loop rather than one global regex parser.
- **Profile**: the configured set of capabilities/tools allowed for this Agent.
- **Fallback**: an explicit source- or role-specific alternate path. The retired
  general chat template and Requests chat-runtime rollback are not current
  production fallbacks; provider failure must remain visible as error/partial.
- **Anchor date**: the reference event's date, such as a stock's limit-up day.
- **Data cutoff / data_as_of**: the latest data boundary allowed for a query or
  recorded prediction. It is different from the report's generation time.
- **Outcome**: what was observed after a recorded signal/prediction. Missing
  future bars do not count as zero returns or failed promotions.
- **Live snapshot**: a recorded published cohort with provenance. A recalculated
  historical observation does not become live merely because it has a score.
- **Cohort / denominator**: the exact set of eligible observations behind a rate.
  Check maturity, missing data and deduplication before interpreting a statistic.
- **Test double / fixture**: controlled data or a replacement dependency used by
  a test. A passing fixture test is not a claim about a live provider's accuracy.

## Reading a function

Read its purpose comment/docstring first, then its inputs and return type. Follow
early-return conditions before the main calculation: these usually explain
unsupported input, missing history, cached results or safety boundaries. In long
functions, the step comments describe why operations occur in that order. Read
the returned model and the calling function together to see how errors and None
values become a visible UI state.

Existing runtime docstrings are retained. New Python comments do not change
`__doc__`, generated API descriptions, prompt text or SQL. Frontend callback
comments are code comments, including inside JSX expressions, and are not UI copy.
